import SwiftUI

@main
struct TrignoApp: App {
    @State private var model = AppModel()
    @Environment(\.scenePhase) private var scenePhase

    var body: some Scene {
        WindowGroup {
            ContentView()
                .environment(model)
                #if DEBUG
                // Development: `-host 127.0.0.1 -autoConnect YES -autoStart YES`
                // [-durationLimit 3] [-triggerStart YES] [-triggerStop YES].
                .task {
                    let defaults = UserDefaults.standard
                    guard defaults.bool(forKey: "autoConnect") else { return }
                    model.durationLimit = defaults.double(forKey: "durationLimit")
                    model.triggerStart = defaults.bool(forKey: "triggerStart")
                    model.triggerStop = defaults.bool(forKey: "triggerStop")
                    await model.connect()
                    if defaults.bool(forKey: "autoStart") { await model.startAcquisition() }
                }
                #endif
        }
        #if os(iOS)
        .onChange(of: scenePhase) { _, phase in
            if phase == .background && model.isAcquiring {
                // iPadOS suspends backgrounded apps, which drops the TCP streams.
                model.alert = "On iPad, recording stops when Trigno leaves the screen."
            }
        }
        #endif
    }
}
