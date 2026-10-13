"""Constants and framing for the Trigno SDK TCP protocol (MAN-025-3-5, SDK v3.5)."""

COMMAND_PORT = 50040
LEGACY_EMG_PORT = 50041
LEGACY_AUX_PORT = 50042
EMG_PORT = 50043
AUX_PORT = 50044

# Number of multiplexed float32 channels carried on each data port (section 6.1).
PORT_CHANNELS = {
    EMG_PORT: 16,
    AUX_PORT: 144,
    LEGACY_EMG_PORT: 16,
    LEGACY_AUX_PORT: 48,
}

MAX_SENSORS = 16
# AUX buffer position of a sensor's first aux channel = EMG start index * 9 (section 6.1.3).
AUX_CHANNELS_PER_SLOT = 9

BYTES_PER_SAMPLE = 4

# Each command ends with <CR><LF>; a packet ends with two consecutive pairs (section 6.2).
TERMINATOR = b"\r\n\r\n"

REPLY_OK = "OK"
REPLY_INVALID = "INVALID COMMAND"
REPLY_CANNOT = "CANNOT COMPLETE"

# Messages the server can push on the command port without being asked.
ASYNC_PREFIXES = ("STOPPED",)
ASYNC_SUFFIXES = ("PAIR COMPLETE",)


def encode_command(command: str) -> bytes:
    return command.strip().encode("ascii") + TERMINATOR


def is_async_message(message: str) -> bool:
    return message.startswith(ASYNC_PREFIXES) or message.endswith(ASYNC_SUFFIXES)
