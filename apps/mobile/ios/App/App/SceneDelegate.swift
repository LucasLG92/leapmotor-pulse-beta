import UIKit
import Capacitor

class SceneDelegate: UIResponder, UIWindowSceneDelegate {
    var window: UIWindow?

    func scene(_ scene: UIScene, willConnectTo session: UISceneSession, options connectionOptions: UIScene.ConnectionOptions) {
        guard let windowScene = scene as? UIWindowScene else { return }

        window = UIWindow(windowScene: windowScene)
        window?.rootViewController = PulseBridgeViewController()
        window?.makeKeyAndVisible()

        SceneDelegateProxy.shared.scene(scene, willConnectTo: session, options: connectionOptions)
    }

    func scene(_ scene: UIScene, openURLContexts URLContexts: Set<UIOpenURLContext>) {
        guard URLContexts.contains(where: { Self.isWidgetOpenURL($0.url) }) else { return }
        // WidgetKit only foregrounds Pulse here; the URL carries no command or data.
    }

    func scene(_ scene: UIScene, continue userActivity: NSUserActivity) {
        // OAuth HTTPS callbacks belong to ASWebAuthenticationSession, never the web bridge.
        guard userActivity.activityType != NSUserActivityTypeBrowsingWeb else { return }
        SceneDelegateProxy.shared.scene(scene, continue: userActivity)
    }

    private static func isWidgetOpenURL(_ url: URL) -> Bool {
        url.scheme == "pulse" && url.host == "open" && url.path.isEmpty
            && url.user == nil && url.password == nil && url.query == nil && url.fragment == nil
    }
}
