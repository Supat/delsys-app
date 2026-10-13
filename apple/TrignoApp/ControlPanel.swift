import SwiftUI
import TrignoKit

struct ControlPanel: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        @Bindable var model = model
        Form {
            Section {
                TextField("IP of PC running Trigno Control Utility", text: $model.host)
                    .textContentType(.URL)
                    .autocorrectionDisabled()
                    #if os(iOS)
                    .keyboardType(.numbersAndPunctuation)
                    .textInputAutocapitalization(.never)
                    #endif
                    .disabled(model.phase != .disconnected)
                    .onSubmit { Task { await model.connect() } }
                if model.isConnected {
                    Button("Disconnect", role: .destructive) { Task { await model.disconnect() } }
                        .disabled(model.isAcquiring)
                } else {
                    Button(model.phase == .connecting ? "Connecting…" : "Connect") { Task { await model.connect() } }
                        .disabled(model.phase == .connecting || model.host.isEmpty)
                }
            } header: {
                Text("SDK server")
            } footer: {
                if let system = model.system {
                    Text("\(system.baseSerial) · EMG \(system.emgRate, format: .number.precision(.fractionLength(1))) Hz · AUX \(system.auxRate, format: .number.precision(.fractionLength(1))) Hz")
                }
            }

            Section {
                Toggle("Save to file", isOn: $model.saveToFile)
                Toggle("Wait for start trigger", isOn: $model.triggerStart)
                Toggle("Stop on stop trigger", isOn: $model.triggerStop)
                LabeledContent("Duration (s)") {
                    TextField("0", value: $model.durationLimit, format: .number)
                        .multilineTextAlignment(.trailing)
                        #if os(iOS)
                        .keyboardType(.decimalPad)
                        #endif
                        .frame(maxWidth: 80)
                }
                Picker("Plot window", selection: $model.plotWindow) {
                    ForEach([2.0, 5, 10, 20], id: \.self) { Text("\(Int($0)) s").tag($0) }
                }
                if model.isConnected {
                    AcquisitionButton()
                }
                if let start = model.dataStartedAt, model.isAcquiring {
                    TimelineView(.periodic(from: .now, by: 0.5)) { context in
                        LabeledContent("Elapsed", value: Duration.seconds(context.date.timeIntervalSince(start)),
                                       format: .time(pattern: .minuteSecond))
                    }
                }
                if !model.lastSampleCounts.isEmpty {
                    LabeledContent("Samples") {
                        Text("EMG \(model.lastSampleCounts[.emg] ?? 0) · AUX \(model.lastSampleCounts[.aux] ?? 0)")
                            .monospacedDigit()
                    }
                }
            } header: {
                Text("Acquisition")
            } footer: {
                #if os(iOS)
                Text("Duration 0 records until stopped. iPad: keep Trigno on screen while recording.")
                #else
                Text("Duration 0 records until stopped.")
                #endif
            }
            .disabled(!model.isConnected)

            if let system = model.system {
                Section {
                    ForEach(system.sensors) { sensor in
                        NavigationLink {
                            SensorDetailView(sensor: sensor)
                        } label: {
                            SensorRow(sensor: sensor)
                        }
                    }
                    if system.sensors.isEmpty {
                        Text("No active sensors. Pair sensors and turn them on, then refresh.")
                            .foregroundStyle(.secondary)
                    }
                    NavigationLink("System settings") { SystemSettingsView() }
                } header: {
                    HStack {
                        Text("Sensors")
                        Spacer()
                        Button { Task { await model.refresh() } } label: {
                            Image(systemName: "arrow.clockwise")
                        }
                        .disabled(model.isAcquiring)
                        .accessibilityLabel("Refresh sensors")
                    }
                }
            }

            Section {
                NavigationLink {
                    RecordingsView()
                } label: {
                    LabeledContent("Recordings", value: "\(model.recordings.count)")
                }
                NavigationLink("Console") { ConsoleView() }
            }
        }
        .formStyle(.grouped)
    }
}

struct AcquisitionButton: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        if model.isAcquiring {
            Button(role: .destructive) { Task { await model.stopAcquisition() } } label: {
                Label("Stop", systemImage: "stop.fill").frame(maxWidth: .infinity)
            }
            .buttonStyle(.borderedProminent)
            .tint(.red)
        } else {
            Button { Task { await model.startAcquisition() } } label: {
                Label(model.triggerStart ? "Arm" : (model.saveToFile ? "Record" : "Start"),
                      systemImage: model.saveToFile ? "record.circle" : "play.fill")
                    .frame(maxWidth: .infinity)
            }
            .buttonStyle(.borderedProminent)
        }
    }
}

struct SensorRow: View {
    let sensor: SensorInfo

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text("Sensor \(sensor.slot)").font(.body.weight(.medium))
            Text("\(sensor.mode) · EMG ×\(sensor.emgChannelCount) · AUX ×\(sensor.auxChannelCount)")
                .font(.caption)
                .foregroundStyle(.secondary)
        }
    }
}
