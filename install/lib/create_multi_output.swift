#!/usr/bin/env swift
// create_multi_output.swift
// Programmatically creates a macOS Multi-Output (Aggregate) Device that bundles:
//   - Built-in speakers (so the user hears audio)
//   - BlackHole (so RTT can capture it)
//
// Usage:  swift create_multi_output.swift [--name "RTT Multi-Output"] [--list]
//
// Why this exists:
//   The "easy" macOS path for capturing system audio is BlackHole + Multi-Output Device.
//   Apple makes you click through Audio MIDI Setup to create one — terrible UX.
//   This script does it in one shell call so install.sh can be fully unattended.

import Foundation
import CoreAudio
import AudioToolbox

// ── CLI args ────────────────────────────────────────────────────────────────
let args = CommandLine.arguments
var aggregateName = "RTT Multi-Output"
var aggregateUID = "com.rtt.multi-output"
var listOnly = false
var i = 1
while i < args.count {
    switch args[i] {
    case "--name": aggregateName = args[i+1]; i += 2
    case "--list": listOnly = true; i += 1
    case "--help", "-h":
        print("Usage: create_multi_output.swift [--name NAME] [--list]")
        exit(0)
    default: i += 1
    }
}

// ── CoreAudio helpers ──────────────────────────────────────────────────────
func getAllDevices() -> [AudioDeviceID] {
    var size: UInt32 = 0
    var addr = AudioObjectPropertyAddress(
        mSelector: kAudioHardwarePropertyDevices,
        mScope: kAudioObjectPropertyScopeGlobal,
        mElement: kAudioObjectPropertyElementMain)
    AudioObjectGetPropertyDataSize(AudioObjectID(kAudioObjectSystemObject), &addr, 0, nil, &size)
    let count = Int(size) / MemoryLayout<AudioDeviceID>.size
    var devices = [AudioDeviceID](repeating: 0, count: count)
    AudioObjectGetPropertyData(AudioObjectID(kAudioObjectSystemObject), &addr, 0, nil, &size, &devices)
    return devices
}

func getCFStringProperty(_ id: AudioDeviceID, selector: AudioObjectPropertySelector) -> String? {
    var addr = AudioObjectPropertyAddress(
        mSelector: selector,
        mScope: kAudioObjectPropertyScopeGlobal,
        mElement: kAudioObjectPropertyElementMain)
    var size = UInt32(MemoryLayout<CFString?>.size)
    var cf: Unmanaged<CFString>?
    let st = withUnsafeMutablePointer(to: &cf) { ptr -> OSStatus in
        return AudioObjectGetPropertyData(id, &addr, 0, nil, &size, ptr)
    }
    guard st == noErr, let result = cf?.takeRetainedValue() else { return nil }
    return result as String
}

func getDeviceUID(_ id: AudioDeviceID) -> String? {
    return getCFStringProperty(id, selector: kAudioDevicePropertyDeviceUID)
}

func getDeviceName(_ id: AudioDeviceID) -> String? {
    return getCFStringProperty(id, selector: kAudioDevicePropertyDeviceNameCFString)
}

func deviceHasOutputStreams(_ id: AudioDeviceID) -> Bool {
    var size: UInt32 = 0
    var addr = AudioObjectPropertyAddress(
        mSelector: kAudioDevicePropertyStreams,
        mScope: kAudioDevicePropertyScopeOutput,
        mElement: kAudioObjectPropertyElementMain)
    AudioObjectGetPropertyDataSize(id, &addr, 0, nil, &size)
    return size > 0
}

func findDeviceUIDByNameContains(_ needle: String) -> String? {
    for dev in getAllDevices() {
        if let n = getDeviceName(dev), n.lowercased().contains(needle.lowercased()) {
            return getDeviceUID(dev)
        }
    }
    return nil
}

func findOutputDeviceUIDByNameContains(_ needle: String) -> String? {
    for dev in getAllDevices() {
        guard deviceHasOutputStreams(dev) else { continue }
        if let n = getDeviceName(dev), n.lowercased().contains(needle.lowercased()) {
            return getDeviceUID(dev)
        }
    }
    return nil
}

// ── List mode: enumerate devices and exit ─────────────────────────────────
if listOnly {
    print("All audio devices:")
    for dev in getAllDevices() {
        let n = getDeviceName(dev) ?? "?"
        let u = getDeviceUID(dev) ?? "?"
        let kind = deviceHasOutputStreams(dev) ? "OUT" : "IN "
        print("  [\(kind)] \(n)  (uid=\(u))")
    }
    exit(0)
}

// ── Find member devices ────────────────────────────────────────────────────
guard let blackholeUID = findDeviceUIDByNameContains("BlackHole") else {
    FileHandle.standardError.write("ERROR: BlackHole not found. Install with: brew install blackhole-2ch\n".data(using: .utf8)!)
    exit(2)
}

// Prefer built-in speakers; fall back to any output device that isn't BlackHole or our own aggregate.
var speakersUID: String? = findOutputDeviceUIDByNameContains("MacBook")
    ?? findOutputDeviceUIDByNameContains("Built-in")
    ?? findOutputDeviceUIDByNameContains("Speakers")

if speakersUID == nil {
    // Last resort: pick the first non-BlackHole non-aggregate output device
    for dev in getAllDevices() {
        guard deviceHasOutputStreams(dev) else { continue }
        let uid = getDeviceUID(dev) ?? ""
        let name = getDeviceName(dev) ?? ""
        if uid.lowercased().contains("blackhole") { continue }
        if uid == aggregateUID { continue }
        if name.lowercased().contains("multi-output") { continue }
        speakersUID = uid
        break
    }
}

guard let speakers = speakersUID else {
    FileHandle.standardError.write("ERROR: Could not find a speaker output device.\n".data(using: .utf8)!)
    exit(3)
}

// ── Check if our aggregate already exists ─────────────────────────────────
for dev in getAllDevices() {
    if getDeviceUID(dev) == aggregateUID {
        print("EXISTS:\(aggregateUID)")
        exit(0)
    }
}

// ── Create the aggregate device ────────────────────────────────────────────
let dict: [String: Any] = [
    kAudioAggregateDeviceNameKey as String: aggregateName,
    kAudioAggregateDeviceUIDKey as String: aggregateUID,
    kAudioAggregateDeviceMasterSubDeviceKey as String: speakers,
    kAudioAggregateDeviceIsStackedKey as String: 1,   // 1 = Multi-Output Device, 0 = Aggregate Device
    kAudioAggregateDeviceSubDeviceListKey as String: [
        [kAudioSubDeviceUIDKey as String: speakers],
        [kAudioSubDeviceUIDKey as String: blackholeUID],
    ],
]

var newDeviceID: AudioDeviceID = 0
let status = AudioHardwareCreateAggregateDevice(dict as CFDictionary, &newDeviceID)
if status != noErr {
    FileHandle.standardError.write("ERROR: AudioHardwareCreateAggregateDevice failed (status=\(status))\n".data(using: .utf8)!)
    exit(4)
}

print("CREATED:\(aggregateUID)")
print("Members:")
print("  - speakers: \(getDeviceName(getAllDevices().first(where: { getDeviceUID($0) == speakers }) ?? 0) ?? speakers)")
print("  - capture:  BlackHole (\(blackholeUID))")
exit(0)
