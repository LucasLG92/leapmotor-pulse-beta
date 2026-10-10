import Capacitor

final class PulseBridgeViewController: CAPBridgeViewController {
    override func capacitorDidLoad() {
        bridge?.registerPluginInstance(PulseNativePlugin())
    }
}
