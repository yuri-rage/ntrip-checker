#!/usr/bin/env python3
"""
ntrip_checker.py -- Copyright 2026 Yuri Rage

A Python script to connect to an NTRIP caster, receive and parse RTCM3 messages
for a user-specified duration, and print a summary of all satellites and bands in use.

Dependencies:
    pip install pyrtcm

Usage:
    # Get sourcetable (list mountpoints)
    python3 ntrip_checker.py --host rtk2go.com --port 2101

    # Connect to a mountpoint and parse messages for 20 seconds
    python3 ntrip_checker.py --host rtk2go.com --port 2101 --mountpoint Andrzej --duration 20
"""

import argparse
import base64
import socket
import sys
import time
from datetime import datetime, timezone

try:
    from pyrtcm import RTCMReader, parse_msm
except ImportError:
    print("Error: 'pyrtcm' library is required but not installed.", file=sys.stderr)
    print("Please install it using your Python environment's pip:", file=sys.stderr)
    print("  pip install pyrtcm", file=sys.stderr)
    sys.exit(1)

# Mapping of GNSS name to PRN prefix
CONSTELLATION_PREFIXES = {
    "GPS": "G",
    "GLONASS": "R",
    "GALILEO": "E",
    "BEIDOU": "C",
    "QZSS": "J",
    "SBAS": "S",
}

# Standard names for common RTCM3 message types
RTCM_MSG_NAMES = {
    "1001": "GPS L1 Basic RTK",
    "1002": "GPS L1 Extended RTK",
    "1003": "GPS L1/L2 Basic RTK",
    "1004": "GPS L1/L2 Extended RTK",
    "1005": "Station ARP (No Height)",
    "1006": "Station ARP (With Height)",
    "1007": "Antenna Descriptor",
    "1008": "Antenna Serial Number",
    "1009": "GLONASS L1 Basic RTK",
    "1010": "GLONASS L1 Extended RTK",
    "1011": "GLONASS L1/L2 Basic RTK",
    "1012": "GLONASS L1/L2 Extended RTK",
    "1013": "System Parameters",
    "1019": "GPS Ephemeris",
    "1020": "GLONASS Ephemeris",
    "1033": "Receiver/Antenna Descriptor",
    "1071": "GPS MSM1",
    "1072": "GPS MSM2",
    "1073": "GPS MSM3",
    "1074": "GPS MSM4",
    "1075": "GPS MSM5",
    "1076": "GPS MSM6",
    "1077": "GPS MSM7",
    "1081": "GLONASS MSM1",
    "1082": "GLONASS MSM2",
    "1083": "GLONASS MSM3",
    "1084": "GLONASS MSM4",
    "1085": "GLONASS MSM5",
    "1086": "GLONASS MSM6",
    "1087": "GLONASS MSM7",
    "1091": "Galileo MSM1",
    "1092": "Galileo MSM2",
    "1093": "Galileo MSM3",
    "1094": "Galileo MSM4",
    "1095": "Galileo MSM5",
    "1096": "Galileo MSM6",
    "1097": "Galileo MSM7",
    "1101": "SBAS MSM1",
    "1102": "SBAS MSM2",
    "1103": "SBAS MSM3",
    "1104": "SBAS MSM4",
    "1105": "SBAS MSM5",
    "1106": "SBAS MSM6",
    "1107": "SBAS MSM7",
    "1111": "QZSS MSM1",
    "1112": "QZSS MSM2",
    "1113": "QZSS MSM3",
    "1114": "QZSS MSM4",
    "1115": "QZSS MSM5",
    "1116": "QZSS MSM6",
    "1117": "QZSS MSM7",
    "1121": "BeiDou MSM1",
    "1122": "BeiDou MSM2",
    "1123": "BeiDou MSM3",
    "1124": "BeiDou MSM4",
    "1125": "BeiDou MSM5",
    "1126": "BeiDou MSM6",
    "1127": "BeiDou MSM7",
    "1230": "GLONASS Bias Info",
}


class SocketStream:
    """
    Custom wrapper around a socket that provides a .read(n) method.
    This avoids the socket-poisoning behavior of socket.makefile()
    which raises 'cannot read from timed out object' after a single timeout.
    """

    def __init__(self, sock):
        self.sock = sock

    def read(self, n: int) -> bytes:
        data = b""
        while len(data) < n:
            try:
                chunk = self.sock.recv(n - len(data))
                if not chunk:
                    break  # EOF / Connection closed
                data += chunk
            except (socket.timeout, TimeoutError):
                if data:
                    return data
                raise socket.timeout("Socket read timed out")
        return data


def make_gga(lat: float, lon: float, alt: float = 100.0) -> str:
    """
    Generates a valid NMEA GGA sentence based on the coordinates.
    Required by some NTRIP casters to enable correction stream (e.g. VRS).
    """
    now = datetime.now(timezone.utc)
    time_str = now.strftime("%H%M%S.00")

    # Format latitude: DDMM.MMMMM (explicitly split to avoid format width edge-cases)
    lat_deg = int(abs(lat))
    lat_min = (abs(lat) - lat_deg) * 60
    lat_min_int = int(lat_min)
    lat_min_dec = int(round((lat_min - lat_min_int) * 100000))
    if lat_min_dec >= 100000:
        lat_min_dec = 0
        lat_min_int += 1
    lat_dir = "N" if lat >= 0 else "S"
    lat_str = f"{lat_deg:02d}{lat_min_int:02d}.{lat_min_dec:05d}"

    # Format longitude: DDDMM.MMMMM
    lon_deg = int(abs(lon))
    lon_min = (abs(lon) - lon_deg) * 60
    lon_min_int = int(lon_min)
    lon_min_dec = int(round((lon_min - lon_min_int) * 100000))
    if lon_min_dec >= 100000:
        lon_min_dec = 0
        lon_min_int += 1
    lon_dir = "E" if lon >= 0 else "W"
    lon_str = f"{lon_deg:03d}{lon_min_int:02d}.{lon_min_dec:05d}"

    # GPGGA fields
    # Quality: 1 (GPS Fix), Sats: 8, HDOP: 1.0
    gga = f"GPGGA,{time_str},{lat_str},{lat_dir},{lon_str},{lon_dir},1,08,1.0,{alt:.1f},M,0.0,M,,"

    # Calculate XOR checksum
    checksum = 0
    for char in gga:
        checksum ^= ord(char)

    return f"${gga}*{checksum:02X}\r\n"


def format_prn(gnss: str, prn: str) -> str:
    """Formats raw PRN to standard format (e.g., G05, R12, E31, S121)."""
    prefix = CONSTELLATION_PREFIXES.get(gnss.upper(), "")
    try:
        val = int(prn)
        if val >= 100:
            return f"{prefix}{val}"
        else:
            return f"{prefix}{val:02d}"
    except (ValueError, TypeError):
        return f"{prefix}{prn}"


def connect_and_handshake(
    host: str,
    port: int,
    mountpoint: str = "",
    user: str | None = None,
    password: str | None = None,
    ntrip_version: str = "1.0",
    timeout: float = 5.0,
) -> socket.socket:
    """
    Connects to the NTRIP caster and performs the HTTP handshake.
    Supports both IPv4 and IPv6 out of the box.
    Returns the connected socket, or raises an error.
    """
    # Connect supporting both IPv4 and IPv6
    s = socket.create_connection((host, port), timeout=timeout)

    path = f"/{mountpoint}" if mountpoint else "/"
    headers = []
    if ntrip_version == "2.0":
        headers.append(f"GET {path} HTTP/1.1")
        headers.append(f"Host: {host}:{port}")
        headers.append("Ntrip-Version: 2.0")
    else:
        headers.append(f"GET {path} HTTP/1.0")

    headers.append("User-Agent: NTRIP ntrip-checker/1.0")
    headers.append("Accept: */*")

    if user is not None:
        pw = password if password is not None else ""
        auth_str = f"{user}:{pw}"
        auth_b64 = base64.b64encode(auth_str.encode("utf-8")).decode("utf-8")
        headers.append(f"Authorization: Basic {auth_b64}")

    headers.append("Connection: close")
    headers.append("")
    headers.append("")

    req = "\r\n".join(headers)
    s.sendall(req.encode("utf-8"))

    # Read HTTP response headers
    response_header = b""
    while b"\r\n\r\n" not in response_header:
        chunk = s.recv(1)
        if not chunk:
            break
        response_header += chunk
        if len(response_header) > 4096:
            break

    header_text = response_header.decode("utf-8", errors="ignore")

    # Safely parse the first header line (the HTTP/ICY status line)
    lines = header_text.split("\r\n")
    status_line = lines[0] if lines else ""

    if "200" not in status_line and "ICY 200" not in status_line:
        s.close()
        raise ConnectionError(
            f"Handshake failed (Status: {status_line}). Caster response headers:\n{header_text}"
        )

    return s


def fetch_sourcetable(
    host: str,
    port: int,
    user: str | None = None,
    password: str | None = None,
    ntrip_version: str = "1.0",
) -> None:
    """Connects to the caster and downloads/prints the mountpoints."""
    try:
        s = connect_and_handshake(
            host=host,
            port=port,
            mountpoint="",
            user=user,
            password=password,
            ntrip_version=ntrip_version,
        )

        response = b""
        while True:
            chunk = s.recv(4096)
            if not chunk:
                break
            response += chunk
        s.close()

        parts = response.split(b"\r\n\r\n", 1)
        body = parts[1].decode("utf-8", errors="ignore") if len(parts) > 1 else ""

        print("\nAvailable Mountpoints (Top 25):")
        print(
            f"{'Mountpoint':<15} | {'Format':<10} | {'Details':<20} | {'Location':<25}"
        )
        print("-" * 80)

        lines = body.split("\n")
        count = 0
        for line in lines:
            if line.startswith("STR;"):
                fields = line.split(";")
                if len(fields) > 4:
                    mount = fields[1]
                    fmt = fields[3]
                    sys_in_use = fields[6] if len(fields) > 6 else ""
                    country = fields[8] if len(fields) > 8 else ""
                    lat = fields[9] if len(fields) > 9 else ""
                    lon = fields[10] if len(fields) > 10 else ""

                    loc = f"{lat}, {lon}" if lat and lon else country
                    details = sys_in_use[:20] if sys_in_use else fmt

                    print(f"{mount:<15} | {fmt:<10} | {details:<20} | {loc:<25}")
                    count += 1
                    if count >= 25:
                        break

        total_mountpoints = sum(1 for line in lines if line.startswith("STR;"))
        print("-" * 80)
        print(f"Showing {count} of {total_mountpoints} total mountpoints.")
        print(
            "\nRun the script again with a specific mountpoint using the --mountpoint argument."
        )

    except Exception as e:
        print(f"Error fetching sourcetable: {e}", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(
        description="NTRIP Checker - Analyze RTCM3 stream and report GNSS satellites/bands in use."
    )
    parser.add_argument(
        "--host",
        default="rtk2go.com",
        help="NTRIP caster hostname/IP (default: rtk2go.com)",
    )
    parser.add_argument(
        "--port", type=int, default=2101, help="NTRIP caster port (default: 2101)"
    )
    parser.add_argument(
        "--mountpoint", help="NTRIP mountpoint name (if omitted, lists the sourcetable)"
    )
    parser.add_argument("--user", default=None, help="NTRIP username")
    parser.add_argument("--password", default=None, help="NTRIP password")
    parser.add_argument(
        "--ntrip-version",
        choices=["1.0", "2.0"],
        default="1.0",
        help="NTRIP protocol version (default: 1.0)",
    )
    parser.add_argument(
        "--duration",
        type=int,
        default=15,
        help="Time to stream and parse messages in seconds (default: 15)",
    )
    parser.add_argument(
        "--lat",
        type=float,
        default=None,
        help="Latitude for NMEA GGA sentence (must specify both lat and lon to send GGA)",
    )
    parser.add_argument(
        "--lon",
        type=float,
        default=None,
        help="Longitude for NMEA GGA sentence (must specify both lat and lon to send GGA)",
    )
    parser.add_argument(
        "--alt",
        type=float,
        default=100.0,
        help="Elevation (meters) for NMEA GGA sentence (default: 100.0)",
    )
    parser.add_argument(
        "--verbose", action="store_true", help="Print messages as they are received"
    )

    args = parser.parse_args()

    if not args.mountpoint:
        fetch_sourcetable(
            args.host, args.port, args.user, args.password, args.ntrip_version
        )
        return

    # Data collections
    msg_counts = {}
    sat_signals = {}  # { GNSS: { formatted_prn: {bands} } }
    station_id = "Unknown"

    # Start connection
    print(f"Initializing connection to {args.host}:{args.port}/{args.mountpoint}...")
    try:
        s = connect_and_handshake(
            host=args.host,
            port=args.port,
            mountpoint=args.mountpoint,
            user=args.user,
            password=args.password,
            ntrip_version=args.ntrip_version,
        )

        # Only send NMEA GGA sentence if coordinates were specified on CLI
        lat_val: float | None = args.lat
        lon_val: float | None = args.lon
        send_gga = lat_val is not None and lon_val is not None
        if send_gga and lat_val is not None and lon_val is not None:
            print("Connected to caster! Sending initial location (NMEA GGA)...")
            gga = make_gga(lat_val, lon_val, args.alt)
            s.sendall(gga.encode("utf-8"))
        else:
            print("Connected to caster! (No coordinates provided, skipping NMEA GGA)")

        # Wrap socket in custom SocketStream to avoid makefile poisoning
        s_stream = SocketStream(s)
        # labelmsm=2 decodes CELLSIG to frequency band labels (e.g. L1, L2, L5, E5)
        reader = RTCMReader(s_stream, labelmsm=2)

        print(
            f"Streaming data. Parsing for {args.duration} seconds (Press Ctrl+C to stop)..."
        )
        print("-" * 50)

        start_time = time.time()
        last_gga_time = start_time
        gga_interval = 10.0  # seconds

        # Set socket timeout to 1 second so we can check duration and send GGA updates
        s.settimeout(1.0)

        while True:
            elapsed = time.time() - start_time
            if elapsed >= args.duration:
                break

            # Periodically resend GGA (only if coordinates were specified)
            now = time.time()
            if (
                send_gga
                and lat_val is not None
                and lon_val is not None
                and now - last_gga_time >= gga_interval
            ):
                try:
                    s.sendall(make_gga(lat_val, lon_val, args.alt).encode("utf-8"))
                    last_gga_time = now
                except socket.error:
                    pass

            try:
                raw, parsed = reader.read()
                if parsed is not None:
                    identity = parsed.identity
                    msg_counts[identity] = msg_counts.get(identity, 0) + 1

                    if hasattr(parsed, "DF003") and station_id == "Unknown":
                        station_id = parsed.DF003

                    if args.verbose:
                        name = RTCM_MSG_NAMES.get(identity, "Unknown RTCM Message")
                        print(f"[{elapsed:5.1f}s] MSG {identity:<4} ({name})")

                    if parsed.ismsm:
                        try:
                            meta, sat_data, cell_data = parse_msm(parsed)
                            gnss = meta.get("gnss", "UNKNOWN")

                            if gnss not in sat_signals:
                                sat_signals[gnss] = {}

                            for cell in cell_data:
                                cell_prn = cell.get("CELLPRN")
                                cell_sig = cell.get("CELLSIG")

                                if cell_prn is not None and cell_sig is not None:
                                    formatted = format_prn(gnss, cell_prn)
                                    if formatted not in sat_signals[gnss]:
                                        sat_signals[gnss][formatted] = set()
                                    sat_signals[gnss][formatted].add(cell_sig)
                        except Exception as e:
                            # Catch any decoding anomalies in specific messages
                            if args.verbose:
                                print(f"Error decoding MSM cell data: {e}")

            except socket.timeout:
                # Normal if message doesn't arrive in 1 sec, continue loop to check timing/GGA
                continue
            except (ConnectionError, socket.error) as e:
                print(f"\nConnection lost: {e}")
                break

        s.close()
        print("\nStream collection complete. Processing results...")

    except KeyboardInterrupt:
        print("\n\nStream collection interrupted by user. Generating report...")
    except Exception as e:
        print(f"\nError establishing or reading stream: {e}", file=sys.stderr)
        return

    # Generate report
    total_messages = sum(msg_counts.values())

    print("\n" + "=" * 70)
    print("                      NTRIP CHECKER REPORT")
    print("=" * 70)
    print(f"Caster:          {args.host}:{args.port}")
    print(f"Mountpoint:      {args.mountpoint}")
    print(f"Station ID:      {station_id}")
    print(f"Total Messages:  {total_messages}")
    print("-" * 70)

    if total_messages == 0:
        print(
            "No RTCM messages were received. Verify the mountpoint is active and does not require private auth."
        )
        print("=" * 70)
        return

    print("Received Message Types:")
    for ident, count in sorted(
        msg_counts.items(), key=lambda x: int(x[0]) if x[0].isdigit() else 9999
    ):
        name = RTCM_MSG_NAMES.get(ident, "Unknown")
        print(f"  - Message {ident:<4} ({name:<25}): {count} messages")
    print("-" * 70)

    if not sat_signals:
        print(
            "No MSM (Multiple Signal Messages) parsed. Satellite/Band breakdown is only available for MSM messages."
        )
        print("=" * 70)
        return

    print("Active GNSS Constellations and Bands Summary:")
    for gnss, sats in sorted(sat_signals.items()):
        all_bands = set()
        for bands in sats.values():
            all_bands.update(bands)
        bands_str = ", ".join(sorted(all_bands))
        print(f"  - {gnss:<10}: {len(sats)} satellites, Active Bands: [{bands_str}]")
    print("-" * 70)

    print("Detailed Satellites & Signal Bands Breakdown:")
    for gnss, sats in sorted(sat_signals.items()):
        print(f"\n[{gnss}]")
        # Sort satellites by PRN
        sorted_sats = sorted(
            sats.items(),
            key=lambda x: (x[0][:1], int(x[0][1:]) if x[0][1:].isdigit() else 999),
        )

        # Display 4 satellites per line to make it neat
        chunk_size = 4
        for i in range(0, len(sorted_sats), chunk_size):
            chunk = sorted_sats[i : i + chunk_size]
            items = []
            for sat, bands in chunk:
                bands_str = "+".join(sorted(bands))
                items.append(f"{sat}: {bands_str}")
            print("  " + "  |  ".join(items))

    print("=" * 70)


if __name__ == "__main__":
    main()
