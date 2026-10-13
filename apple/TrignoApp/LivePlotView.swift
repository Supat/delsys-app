import SwiftUI
import TrignoKit

/// Scrolling plots for every active sensor, redrawn at display rate from `SignalBuffers`.
struct LivePlotView: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        if let system = model.system, !system.sensors.isEmpty {
            ScrollView {
                LazyVStack(spacing: 12) {
                    ForEach(system.sensors) { sensor in
                        SensorCard(sensor: sensor, window: model.plotWindow)
                    }
                }
                .padding()
            }
            #if os(iOS)
            .background(Color(uiColor: .systemGroupedBackground))
            #endif
        } else if model.system != nil {
            ContentUnavailableView("No active sensors", systemImage: "sensor",
                                   description: Text("Pair sensors in the Trigno Control Utility, turn them on and refresh."))
        } else {
            ContentUnavailableView("Not connected", systemImage: "network",
                                   description: Text("Enter the IP of the Windows PC running the Trigno Control Utility and connect."))
        }
    }
}

private struct SensorCard: View {
    let sensor: SensorInfo
    let window: Double

    /// AUX channels grouped by units so g and deg/s get their own scales.
    private var auxGroups: [(units: String, channels: [ChannelInfo])] {
        var order: [String] = []
        var groups: [String: [ChannelInfo]] = [:]
        for channel in sensor.channels(on: .aux) {
            if groups[channel.units] == nil { order.append(channel.units) }
            groups[channel.units, default: []].append(channel)
        }
        return order.map { ($0, groups[$0]!) }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .firstTextBaseline) {
                Text("Sensor \(sensor.slot)").font(.headline)
                Text("\(sensor.serial) · \(sensor.mode)").font(.caption).foregroundStyle(.secondary)
            }
            ForEach(sensor.channels(on: .emg)) { channel in
                PlotStrip(title: "EMG ch\(channel.number)", units: channel.units, port: .emg,
                          channels: [channel], symmetric: true, window: window)
                    .frame(height: 110)
            }
            ForEach(auxGroups, id: \.units) { group in
                PlotStrip(title: "AUX", units: group.units, port: .aux,
                          channels: group.channels, symmetric: false, window: window)
                    .frame(height: 90)
            }
        }
        .padding(12)
        .background(.background, in: RoundedRectangle(cornerRadius: 12))
    }
}

private struct PlotStrip: View {
    @Environment(AppModel.self) private var model
    let title: String
    let units: String
    let port: DataPort
    let channels: [ChannelInfo]
    let symmetric: Bool
    let window: Double

    static let palette: [Color] = [.blue, .orange, .green, .purple, .pink, .teal, .yellow, .indigo, .brown]

    var body: some View {
        TimelineView(.animation(minimumInterval: 1.0 / 30)) { timeline in
            // The frame date is passed in so SwiftUI sees a changed input and redraws.
            PlotCanvas(frame: timeline.date) { context, size in
                draw(in: &context, size: size)
            }
        }
        .overlay(alignment: .topLeading) {
            HStack(spacing: 8) {
                Text(title).fontWeight(.medium)
                ForEach(Array(channels.enumerated()), id: \.offset) { i, channel in
                    if channels.count > 1 {
                        Text("ch\(channel.number)").foregroundStyle(Self.palette[i % Self.palette.count])
                    }
                }
                Text(units).foregroundStyle(.secondary)
            }
            .font(.caption2)
            .padding(4)
        }
        .background(Color.secondary.opacity(0.06), in: RoundedRectangle(cornerRadius: 6))
    }

    private func draw(in context: inout GraphicsContext, size: CGSize) {
        let bins = max(1, Int(size.width))
        let envelopes = channels.map { model.buffers.envelope(port: port, column: $0.column, bins: bins) }
        let filled = envelopes.filter { !$0.isEmpty }
        guard !filled.isEmpty else { return }

        var lo = filled.map(\.lo).min()!
        var hi = filled.map(\.hi).max()!
        if symmetric {
            let peak = max(abs(lo), abs(hi), 1e-9)
            lo = -peak
            hi = peak
        }
        if hi - lo < 1e-9 { lo -= 0.5; hi += 0.5 }
        let pad = (hi - lo) * 0.08
        lo -= pad
        hi += pad
        let topInset: CGFloat = 16
        let plotHeight = size.height - topInset
        func y(_ v: Float) -> CGFloat { topInset + CGFloat((hi - v) / (hi - lo)) * plotHeight }

        if lo < 0 && hi > 0 {
            var zero = Path()
            zero.move(to: CGPoint(x: 0, y: y(0)))
            zero.addLine(to: CGPoint(x: size.width, y: y(0)))
            context.stroke(zero, with: .color(.secondary.opacity(0.3)), lineWidth: 0.5)
        }

        for (i, env) in envelopes.enumerated() where !env.isEmpty {
            let count = env.mins.count
            let dx = size.width / CGFloat(count)
            var path = Path()
            var started = false
            for b in 0..<count where !env.mins[b].isNaN {
                let x = (CGFloat(b) + 0.5) * dx
                let p1 = CGPoint(x: x, y: y(env.mins[b]))
                let p2 = CGPoint(x: x, y: y(env.maxs[b]))
                if started { path.addLine(to: p1) } else { path.move(to: p1); started = true }
                if env.maxs[b] != env.mins[b] { path.addLine(to: p2) }
            }
            context.stroke(path, with: .color(Self.palette[i % Self.palette.count]), lineWidth: 1)
        }

        let scale = Text("\(format(hi - pad))  /  \(format(lo + pad))  ·  \(Int(window)) s")
            .font(.caption2.monospacedDigit())
            .foregroundStyle(.secondary)
        context.draw(scale, at: CGPoint(x: size.width - 4, y: 2), anchor: .topTrailing)
    }

    private func format(_ v: Float) -> String {
        let a = abs(v)
        if a != 0 && (a < 0.01 || a >= 10000) { return String(format: "%.2e", v) }
        return String(format: "%.3g", v)
    }
}

private struct PlotCanvas: View {
    let frame: Date
    let renderer: (inout GraphicsContext, CGSize) -> Void

    var body: some View {
        Canvas { context, size in renderer(&context, size) }
            .id(frame)
    }
}
