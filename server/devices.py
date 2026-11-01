# Output devices by name, for settings' "outputDevice": names stay the same across restarts,
# where PortAudio's numbers change whenever a device comes or goes.


def outputs(sd):
    """Names of the devices that play sound, through the platform's main audio system only
    (Windows lists each device once per audio system)."""
    try:
        main = sd.query_devices(kind="output")["hostapi"]
    except Exception:
        main = None
    names = []
    for d in sd.query_devices():
        if d["max_output_channels"] > 0 and (main is None or d["hostapi"] == main) and d["name"] not in names:
            names.append(d["name"])
    return names


def find(sd, name):
    """PortAudio's number for the output device called `name`, or None when it is not connected."""
    try:
        main = sd.query_devices(kind="output")["hostapi"]
    except Exception:
        main = None
    found = [i for i, d in enumerate(sd.query_devices()) if d["name"] == name and d["max_output_channels"] > 0]
    return next((i for i in found if sd.query_devices(i)["hostapi"] == main), found[0] if found else None)
