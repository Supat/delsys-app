import SwiftUI
import TrignoKit

struct ContentView: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        @Bindable var model = model
        NavigationSplitView {
            ControlPanel()
                .navigationTitle("Trigno")
                #if os(macOS)
                .navigationSplitViewColumnWidth(min: 300, ideal: 340)
                #endif
        } detail: {
            LivePlotView()
                .navigationTitle(model.system == nil ? "Not connected" : "Live")
                .toolbar { StatusToolbar() }
        }
        .alert("Trigno", isPresented: Binding(get: { model.alert != nil }, set: { if !$0 { model.alert = nil } })) {
            Button("OK", role: .cancel) {}
        } message: {
            Text(model.alert ?? "")
        }
    }
}

struct StatusToolbar: ToolbarContent {
    @Environment(AppModel.self) private var model

    var body: some ToolbarContent {
        ToolbarItem(placement: .status) {
            StatusBadge(phase: model.phase, recording: model.currentRecording != nil)
        }
        ToolbarItem(placement: .primaryAction) {
            Group {
                if model.isConnected {
                    if model.isAcquiring {
                        Button { Task { await model.stopAcquisition() } } label: {
                            Label("Stop", systemImage: "stop.fill")
                        }
                        .tint(.red)
                    } else {
                        Button { Task { await model.startAcquisition() } } label: {
                            Label(model.saveToFile ? "Record" : "Start", systemImage: model.saveToFile ? "record.circle" : "play.fill")
                        }
                    }
                }
            }
        }
    }
}

struct StatusBadge: View {
    let phase: AppModel.Phase
    let recording: Bool

    var body: some View {
        let (text, color): (String, Color) = switch phase {
        case .disconnected: ("Offline", .secondary)
        case .connecting: ("Connecting…", .orange)
        case .connected: ("Ready", .green)
        case .armed: ("Waiting for trigger", .orange)
        case .streaming: (recording ? "Recording" : "Streaming", .red)
        }
        HStack(spacing: 8) {
            Circle().fill(color).frame(width: 9, height: 9)
            Text(text).font(.callout.weight(.medium))
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 6)
        .fixedSize()
    }
}
