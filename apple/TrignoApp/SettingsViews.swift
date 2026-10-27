import SwiftUI
import TrignoKit
import TrignoLSL

struct SensorDetailView: View {
    @Environment(AppModel.self) private var model
    let sensor: SensorInfo
    @State private var mode: Int?
    @State private var range = ""
    @State private var bandwidth = ""

    var body: some View {
        Form {
            Section("Sensor \(sensor.slot)") {
                LabeledContent("Type", value: sensor.type)
                LabeledContent("Serial", value: sensor.serial)
                LabeledContent("Firmware", value: sensor.firmware)
                LabeledContent("Mode", value: sensor.mode)
            }

            Section("Channels") {
                ForEach(sensor.channels) { channel in
                    LabeledContent {
                        Text("\(channel.rate ?? 0, format: .number.precision(.fractionLength(1))) Hz · \(channel.units)")
                            .monospacedDigit()
                    } label: {
                        Text("ch\(channel.number) — \(channel.port.rawValue.uppercased())[\(channel.column)]")
                    }
                }
            }

            Section {
                LabeledContent("New mode") {
                    TextField("e.g. 9", value: $mode, format: .number)
                        .multilineTextAlignment(.trailing)
                        #if os(iOS)
                        .keyboardType(.numberPad)
                        #endif
                }
                Picker("EMG range", selection: $range) {
                    Text("Unchanged").tag("")
                    Text("Low ±5.5 mV").tag("LOW")
                    Text("High ±11 mV").tag("HIGH")
                }
                Picker("EMG bandwidth", selection: $bandwidth) {
                    Text("Unchanged").tag("")
                    Text("Narrow 20–450 Hz").tag("NARROW")
                    Text("Wide 10–850 Hz").tag("WIDE")
                }
                Button("Apply") {
                    Task {
                        await model.configureSensor(slot: sensor.slot, mode: mode,
                                                    range: range.isEmpty ? nil : range,
                                                    bandwidth: bandwidth.isEmpty ? nil : bandwidth)
                        mode = nil
                        range = ""
                        bandwidth = ""
                    }
                }
                .disabled(mode == nil && range.isEmpty && bandwidth.isEmpty)
            } header: {
                Text("Configure")
            } footer: {
                Text("Mode numbers are listed in section 7 of the Trigno SDK User Guide (e.g. Avanti 9 = EMG + IMU ±4g, ±250dps). Range and bandwidth apply to Avanti sensors. Settings can't be changed while streaming.")
            }
            .disabled(model.isAcquiring)
        }
        .formStyle(.grouped)
        .navigationTitle("Sensor \(sensor.slot)")
    }
}

struct SystemSettingsView: View {
    @Environment(AppModel.self) private var model
    @State private var pairSlot = 1

    var body: some View {
        Form {
            if let system = model.system {
                Section("Base station") {
                    LabeledContent("Server", value: system.version)
                    LabeledContent("Serial", value: system.baseSerial)
                    LabeledContent("Firmware", value: system.baseFirmware)
                    LabeledContent("Frame interval", value: "\(system.frameInterval * 1000) ms")
                    LabeledContent("Triggers", value: system.trigger)
                }

                Section {
                    Toggle("Backwards compatibility", isOn: Binding(
                        get: { system.backwardsCompatibility.uppercased().contains("YES") },
                        set: { on in Task { await model.setBackwardsCompatibility(on) } }
                    ))
                    Toggle("Upsample EMG to 2000 Hz", isOn: Binding(
                        get: { system.upsampling.uppercased().contains("ON") },
                        set: { on in Task { await model.setUpsampling(on) } }
                    ))
                    LabeledContent("EMG port rate", value: "\(system.emgRate.formatted(.number.precision(.fractionLength(2)))) Hz")
                    LabeledContent("AUX port rate", value: "\(system.auxRate.formatted(.number.precision(.fractionLength(2)))) Hz")
                } header: {
                    Text("Sampling")
                } footer: {
                    Text("With backwards compatibility on, EMG is locked to 2000 Hz (upsampled) or 1925.9 Hz and AUX to 148.1 Hz. Turn it off to stream at the sensors' native rates.")
                }
                .disabled(model.isAcquiring)

                Section {
                    Stepper("Slot \(pairSlot)", value: $pairSlot, in: 1...16)
                    Button("Start pairing") { Task { await model.pair(slot: pairSlot) } }
                } header: {
                    Text("Pair sensor")
                }
                .disabled(model.isAcquiring)
            }
        }
        .formStyle(.grouped)
        .navigationTitle("System settings")
    }
}

struct ConsoleView: View {
    @Environment(AppModel.self) private var model
    @State private var command = ""

    var body: some View {
        VStack(spacing: 0) {
            ScrollViewReader { proxy in
                List(model.log) { entry in
                    HStack(alignment: .firstTextBaseline) {
                        Text(entry.time, format: .dateTime.hour().minute().second())
                            .foregroundStyle(.secondary)
                        Text(entry.text)
                            .foregroundStyle(entry.isError ? .red : .primary)
                            .textSelection(.enabled)
                    }
                    .font(.caption.monospaced())
                    .id(entry.id)
                }
                .onChange(of: model.log.count) {
                    if let last = model.log.last { proxy.scrollTo(last.id) }
                }
            }
            HStack {
                TextField("SDK command, e.g. SENSOR 1 MODE?", text: $command)
                    .textFieldStyle(.roundedBorder)
                    .font(.body.monospaced())
                    .autocorrectionDisabled()
                    #if os(iOS)
                    .textInputAutocapitalization(.characters)
                    #endif
                    .onSubmit(send)
                Button("Send", action: send)
                    .disabled(command.isEmpty || !model.isConnected)
            }
            .padding()
        }
        .navigationTitle("Console")
    }

    private func send() {
        let text = command
        command = ""
        Task { await model.sendRaw(text) }
    }
}

struct RecordingsView: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        List {
            ForEach(model.recordings) { recording in
                Section {
                    ForEach(recording.files, id: \.self) { file in
                        ShareLink(item: file) {
                            Label(file.lastPathComponent, systemImage: "doc.text")
                        }
                    }
                } header: {
                    HStack {
                        Text(recording.name)
                        Spacer()
                        ShareLink(items: recording.files) {
                            Image(systemName: "square.and.arrow.up")
                        }
                        #if os(macOS)
                        Button {
                            NSWorkspace.shared.activateFileViewerSelecting([recording.url])
                        } label: {
                            Image(systemName: "folder")
                        }
                        .buttonStyle(.borderless)
                        #endif
                        Button(role: .destructive) { model.delete(recording) } label: {
                            Image(systemName: "trash")
                        }
                        .buttonStyle(.borderless)
                        .disabled(recording.url == model.currentRecording)
                    }
                }
            }
        }
        .overlay {
            if model.recordings.isEmpty {
                ContentUnavailableView("No recordings", systemImage: "waveform",
                                       description: Text("Recordings are saved as CSV + metadata.json. On iPad they are also visible in the Files app under On My iPad › Trigno."))
            }
        }
        .navigationTitle("Recordings")
        .onAppear { model.reloadRecordings() }
    }
}

struct LSLSettingsView: View {
    @Environment(AppModel.self) private var model
    #if os(iOS)
    @State private var addresses: [LocalIPv4Address] = []
    @State private var copied = false
    #endif

    var body: some View {
        @Bindable var model = model
        Form {
            Section {
                LabeledContent("Name") { TextField("Delsys", text: $model.lsl.name).multilineTextAlignment(.trailing) }
                LabeledContent("Type") { TextField("EMG Signal", text: $model.lsl.type).multilineTextAlignment(.trailing) }
                LabeledContent("Source ID") { TextField("EMG_Signal", text: $model.lsl.sourceID).multilineTextAlignment(.trailing) }
            } header: {
                Text("EMG stream")
            } footer: {
                Text("Defaults match the NI-DAQ app (Daq_revised.py), so LabRecorder and existing scripts find the stream unchanged.")
            }
            .autocorrectionDisabled()
            .disabled(model.isAcquiring)

            Section {
                Picker("Channels", selection: $model.lsl.layout) {
                    Text("16 slots (DAQ layout)").tag(LSLChannelLayout.daqCompatible)
                    Text("Active sensors only").tag(LSLChannelLayout.activeChannels)
                }
                LabeledContent("EMG scale") {
                    TextField("1", value: $model.lsl.emgScale, format: .number)
                        .multilineTextAlignment(.trailing)
                        #if os(iOS)
                        .keyboardType(.decimalPad)
                        #endif
                }
                Toggle("Also stream AUX (\(model.lsl.name) AUX)", isOn: $model.lsl.includeAux)
            } header: {
                Text("Channels")
            } footer: {
                Text("16 slots keeps channel n = sensor slot n, with zeros for empty slots. The SDK reports volts at the electrode; the DAQ recorded the base's amplified analog output, so set EMG scale to that gain if consumers expect the old magnitudes. AUX carries IMU channels at the AUX port rate.")
            }
            .disabled(model.isAcquiring)

            #if os(iOS)
            discoverySection
            #endif

            Section {
                Button("Restore defaults") { model.lsl = LSLConfiguration() }
            } footer: {
                Text("liblsl \(LSLStreamer.libraryVersion).")
            }
            .disabled(model.isAcquiring)
        }
        .formStyle(.grouped)
        .navigationTitle("LSL")
    }

    #if os(iOS)
    /// iPadOS blocks the UDP multicast that LSL uses for discovery, so receivers need
    /// this iPad's address in `KnownPeers`. Show it, with the config lines to paste.
    private var discoverySection: some View {
        Section {
            if addresses.isEmpty {
                Text("Not connected to Wi-Fi or Ethernet.")
                    .foregroundStyle(.secondary)
            } else {
                ForEach(addresses) { item in
                    LabeledContent(item.displayName) {
                        Text(item.address).monospaced().textSelection(.enabled)
                    }
                }
                LabeledContent("lsl_api.cfg") {
                    Text(LocalNetwork.knownPeersConfig(for: addresses))
                        .monospaced()
                        .multilineTextAlignment(.trailing)
                        .textSelection(.enabled)
                }
                Button(copied ? "Copied" : "Copy lsl_api.cfg lines") {
                    UIPasteboard.general.string = LocalNetwork.knownPeersConfig(for: addresses)
                    copied = true
                    Task {
                        try? await Task.sleep(for: .seconds(2))
                        copied = false
                    }
                }
            }
        } header: {
            Text("Discovery on the receiving computer")
        } footer: {
            Text("iPadOS does not allow the multicast LSL uses for discovery, so LabRecorder and other receivers will not find this iPad's streams by themselves. On the receiving computer, save the lines above in a file named lsl_api.cfg in LabRecorder's folder (Windows) or in ~/lsl_api/ (Mac, Linux), then restart the receiver. It will then ask this iPad directly. Give the iPad a fixed IP or DHCP reservation so the entry stays valid.")
        }
        .task {
            while !Task.isCancelled {
                addresses = LocalNetwork.ipv4Addresses()
                try? await Task.sleep(for: .seconds(3))
            }
        }
    }
    #endif
}
