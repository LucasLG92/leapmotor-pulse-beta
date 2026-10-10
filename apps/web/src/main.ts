import { mount } from 'svelte';
import App from './App.svelte';
import './app.css';

const target = document.getElementById('app')!;
target.replaceChildren();
mount(App, { target });
