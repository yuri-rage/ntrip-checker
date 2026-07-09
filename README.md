# NTRIP Checker

A Python command-line utility to connect to an NTRIP caster, stream RTCM3 correction messages for a specified period, parse them, and report which satellites and signal bands (frequencies) are active and in use.

> **Notice:** This project contains LLM-assisted code. All code has been reviewed, verified, and tested by a human.

## Installation

```bash
# Create and activate virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

## Usage

*Note: Ensure your virtual environment is active (`source .venv/bin/activate`) before running commands.*

### 1. Find Available Mountpoints (Sourcetable Discovery)
If you don't know the mountpoint name, omit the `--mountpoint` option:
```bash
python3 ntrip_checker.py --host rtk2go.com --port 2101
```

### 2. Stream and Generate Report
Stream and parse correction data for a given mountpoint (e.g., `Andrzej`) for 15 seconds:
```bash
python3 ntrip_checker.py --host rtk2go.com --port 2101 --mountpoint Andrzej --duration 15
```

### 3. Detailed Command Line Options
```text
options:
  -h, --help            show this help message and exit
  --host HOST           NTRIP caster hostname/IP (default: rtk2go.com)
  --port PORT           NTRIP caster port (default: 2101)
  --mountpoint MOUNTPOINT
                        NTRIP mountpoint name (if omitted, lists the sourcetable)
  --user USER           NTRIP username
  --password PASSWORD   NTRIP password
  --ntrip-version {1.0,2.0}
                        NTRIP protocol version (default: 1.0)
  --duration DURATION   Time to stream and parse messages in seconds (default: 15)
  --lat LAT             Latitude for NMEA GGA sentence (must specify both lat and lon to send GGA)
  --lon LON             Longitude for NMEA GGA sentence (must specify both lat and lon to send GGA)
  --alt ALT             Elevation (meters) for NMEA GGA sentence (default: 100.0)
  --verbose             Print messages as they are received
```

## Examples

### Authenticating with Secured Casters
For casters requiring username and password credentials:
```bash
python3 ntrip_checker.py --host mycaster.com --port 2101 --mountpoint MY_MOUNT --user myusername --password mypassword
```

### Requesting NTRIP v2.0
To explicitly use NTRIP v2.0 (sends HTTP/1.1 with Ntrip-Version header):
```bash
python3 ntrip_checker.py --host rtk2go.com --port 2101 --mountpoint Andrzej --user myemail@example.com --ntrip-version 2.0
```

### Sending NMEA GGA (For VRS / NEAR Mountpoints)
To transmit your current location back to the caster (e.g., Virtual Reference Station mountpoints):
```bash
python3 ntrip_checker.py --host rtk2go.com --port 2101 --mountpoint Andrzej --user myemail@example.com --lat 45.0215 --lon 18.1254 --alt 120.0
```

### Running with Verbose Output
To see each RTCM message ID and metadata printed to standard output in real-time as it arrives:
```bash
python3 ntrip_checker.py --host rtk2go.com --port 2101 --mountpoint Andrzej --user myemail@example.com --duration 5 --verbose
```
