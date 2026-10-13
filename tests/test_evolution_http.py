"""New manual tools preserve session/CSRF and archive owner boundaries."""
import pytest
from fastapi.testclient import TestClient
from pulse.app import create_app
from pulse.config import Config
from pulse.domain import DomainError


@pytest.fixture
def clients(tmp_path):
    config = Config(auth_mode='oidc',public_url='https://pulse.example.org',
        oidc_issuer='https://id.example.org',oidc_client_id='test',
        oidc_client_secret='synthetic',allowed_users=('a','b'),hosts=('pulse.example.org',))
    app = create_app(tmp_path/'tools.sqlite3',config=config)
    with TestClient(app,base_url=config.public_url) as first:
        second = TestClient(app,base_url=config.public_url)
        for client,owner in [(first,'a'),(second,'b')]:
            sid,record=app.state.sessions.create({'id':owner,'name':'Fixture'})
            client.cookies.set(config.cookie_name,sid,domain='pulse.example.org',path='/')
            client.headers['X-Pulse-CSRF']=record.csrf
        yield first,second,app
        second.close()


def test_favorites_are_private_deduplicated_and_restorable(clients):
    a,b,app=clients
    values={'name':'Casa','latitude':40.4,'longitude':-3.7}
    x=a.post('/api/navigation/favorites',json=values)
    assert x.status_code==200,x.text
    item=x.json()
    assert a.post('/api/navigation/favorites',json=values).json()==item
    assert b.get('/api/navigation/favorites').json()=={'items':[]}
    assert b.request('DELETE','/api/navigation/favorites/'+item['id'],json={}).status_code==404
    assert a.post('/api/navigation/favorites',json=values,headers={'X-Pulse-CSRF':'wrong'}).status_code==403
    archive=app.state.coordinator.store.export_data(owner_id='a')
    assert archive['schema_version']==17
    app.state.coordinator.store.import_data(archive,owner_id='a')
    assert a.get('/api/navigation/favorites').json()['items']==[item]
    with pytest.raises(DomainError):
        app.state.coordinator.store.import_data(archive,owner_id='b')


def test_maintenance_plan_completion_preserves_history_and_revision(clients):
    a,b,app=clients
    values={'title':'Filtro','interval_months':12,'interval_km':20000,
        'last_date':'2025-10-12','last_odometer_km':1000,'notes':'Plan personal'}
    response=a.post('/api/maintenance-plans',json=values)
    assert response.status_code==200,response.text
    plan=response.json()
    assert b.get('/api/maintenance-plans').json()['items']==[]
    response=a.post('/api/maintenance-plans/'+plan['id']+'/complete',json={
        'revision':plan['revision'],'performed_date':'2026-10-12','odometer_km':22000,'cost_eur':None})
    assert response.status_code==200,response.text
    done=response.json()
    assert done['revision']>plan['revision']
    assert done['history'][-1]['cost_eur'] is None
    assert a.request('DELETE','/api/maintenance-plans/'+plan['id'],json={'revision':plan['revision']}).status_code==409
    archive=app.state.coordinator.store.export_data(owner_id='a')
    app.state.coordinator.store.import_data(archive,owner_id='a')
    restored=a.get('/api/maintenance-plans?odometer_km=22000').json()['items'][0]
    assert restored['history']==done['history']


def test_plan_is_informational_with_explicit_gaps(clients):
    a,_,_=clients
    data={'energy_kwh':10,'power_kw':7,'efficiency_pct':90,
        'earliest_at':'2026-10-13T00:00:00Z','deadline_at':'2026-10-13T03:00:00Z',
        'tariffs':[{'start_at':'2026-10-13T00:00:00Z','end_at':'2026-10-13T01:00:00Z','price_eur_kwh':0.2}]}
    response=a.post('/api/energy/plan',json=data)
    assert response.status_code==200,response.text
    plan=response.json()
    assert plan['purpose']=='informational_estimate'
    assert plan['coverage']['complete'] is False
    assert plan['estimated_savings_eur'] is None
    assert a.get('/api/energy/comparison?days=30').status_code==200
    assert a.get('/api/energy/comparison?days=31').status_code==422


def test_document_export_has_no_store_and_escaped_notes(clients):
    a,_,_=clients
    token=a.post('/api/journeys/requests',json={}).json()['id']
    response=a.post('/api/journeys',json={'request_id':token,
        'started_at':'2026-10-12T09:00:00Z','ended_at':'2026-10-12T10:00:00Z',
        'from_label':'=HYPERLINK("x")','to_label':'Destino','distance_km':5,
        'energy_kwh':None,'stops':[],'notes':'<script>alert(1)</script>','tags':['Trabajo']})
    assert response.status_code==200,response.text
    html=a.get('/api/journeys/document?days=90&format=print')
    assert html.status_code==200,html.text
    assert '<script>alert(1)</script>' not in html.text
    assert '&lt;script&gt;' in html.text
    assert html.headers['cache-control']=='no-store'
    assert "script-src 'self'" in html.headers['content-security-policy']
    csv=a.get('/api/journeys/document?days=90&format=csv')
    assert csv.status_code==200,csv.text
    assert "'=HYPERLINK" in csv.text
    assert csv.headers['cache-control']=='no-store'


def test_plan_delete_returns_success_and_publishes_revision(clients):
    a,_,app=clients
    result=a.post('/api/maintenance-plans',json={'title':'Revisión','interval_months':12,'last_date':'2026-01-01'}).json()
    before=app.state.coordinator.revision
    response=a.request('DELETE','/api/maintenance-plans/'+result['id'],json={'revision':result['revision']})
    assert response.status_code==200,response.text
    assert response.json()=={'ok':True}
    assert app.state.coordinator.revision==before+1
    assert a.get('/api/maintenance-plans?offset=100000000000000000000000').status_code==422


def test_http_plan_creation_replays_original_intent_without_duplicate(clients):
    a,_,_=clients
    values={'title':'Filtros','interval_months':12,'last_date':'2026-01-01','request_id':'a'*32}
    first=a.post('/api/maintenance-plans',json=values)
    assert first.status_code==200,first.text
    retry=a.post('/api/maintenance-plans',json=values)
    assert retry.status_code==200 and retry.json()['id']==first.json()['id']
    assert a.get('/api/maintenance-plans').json()['total']==1
    assert a.post('/api/maintenance-plans',json=values|{'title':'Otro intento'}).status_code==409


def test_restore_quota_rejection_does_not_create_recovery_copies(clients, monkeypatch):
    from pulse import maintenance_plans

    a, _, app = clients
    archive = app.state.coordinator.store.export_data(owner_id='a')
    created = a.post('/api/maintenance-plans', json={
        'title': 'Revisión', 'interval_months': 12, 'last_date': '2026-01-01',
        'request_id': 'b' * 32,
    })
    assert created.status_code == 200
    monkeypatch.setattr(maintenance_plans, 'MAX_INVALIDATED_MAINTENANCE_PLAN_IDS_PER_OWNER', 0)
    backups = []
    monkeypatch.setattr(app.state.coordinator, '_backup', lambda: backups.append(True))
    epoch = app.state.coordinator.import_epoch
    for _ in range(3):
        response = a.post('/api/import', json={'confirmed': True, 'data': archive})
        assert response.status_code == 429, response.text
    assert backups == []
    assert app.state.coordinator.import_epoch == epoch
    assert a.get('/api/maintenance-plans').json()['total'] == 1


def test_favorite_global_quota_is_checked_before_restore_backup(clients, monkeypatch):
    a, _, app = clients
    result = a.post('/api/navigation/favorites', json={
        'name': 'Destino sintético', 'latitude': 40, 'longitude': -3,
    })
    assert result.status_code == 200
    store = app.state.coordinator.store
    archive = store.export_data(owner_id='a')
    with store._transaction() as connection:
        connection.execute('DELETE FROM navigation_favorites WHERE owner_id = ?', ('a',))
        connection.executemany(
            'INSERT INTO navigation_favorites VALUES (?, ?, ?, ?, ?, ?)',
            [(f'other-{index // 100}', f'{index:032x}', f'Lugar {index}', 40, -3, store._now())
             for index in range(1000)],
        )
    backups = []
    monkeypatch.setattr(app.state.coordinator, '_backup', lambda: backups.append(True))
    for _ in range(3):
        response = a.post('/api/import', json={'confirmed': True, 'data': archive})
        assert response.status_code == 409, response.text
    assert backups == []
    assert a.get('/api/navigation/favorites').json() == {'items': []}
