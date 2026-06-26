import asyncio
import os
import pathlib
import pprint
import sys

import yaml

from custom_components.aux_cloud.api.aux_cloud import (
    AuxCloudAPI,
    parse_device_stats_total,
)


def get_config_path():
    current_dir = pathlib.Path(__file__).parent
    return current_dir / "docs" / "dev" / "config.yaml"


def load_config() -> dict:
    """Load credentials from config file or environment variables."""
    config_path = get_config_path()
    if config_path.is_file():
        with open(config_path, "r", encoding="utf-8") as f:
            return yaml.load(f, Loader=yaml.FullLoader) or {}

    email = os.environ.get("AUX_EMAIL")
    password = os.environ.get("AUX_PASSWORD")
    if email and password:
        return {
            "email": email,
            "password": password,
            "region": os.environ.get("AUX_REGION", "eu"),
            "shared": os.environ.get("AUX_SHARED", "false").lower() == "true",
        }

    print(
        "No credentials found.\n\n"
        "Option 1 — create a config file:\n"
        f"  copy docs/dev/config.yaml.example to {config_path}\n"
        "  and fill in your email, password, and region.\n\n"
        "Option 2 — set environment variables:\n"
        "  $env:AUX_EMAIL = 'your@email.com'\n"
        "  $env:AUX_PASSWORD = 'yourpassword'\n"
        "  $env:AUX_REGION = 'usa'\n"
        "  python demo.py\n"
    )
    sys.exit(1)


if __name__ == "__main__":
    config = load_config()
    email: str = config["email"]
    password: str = config["password"]
    shared: bool = config.get("shared", False)
    region: str = config.get("region", "eu")

    async def main():
        cloud = AuxCloudAPI(region=region)
        await cloud.login(email, password)

        families = await cloud.get_families()
        for family in families:
            print(f"FamilyId {family['familyid']}:")
            devices = await cloud.get_devices(family["familyid"], shared)
            if not devices:
                continue

            print("Devices:")
            pprint.pprint(devices)
            for device in devices:
                print(f"\n--- Stats for {device.get('friendlyName')} ---")
                for report_type in ("day", "month", "year"):
                    try:
                        raw = await cloud.get_device_stats(device, report_type)
                        total = parse_device_stats_total(raw)
                        print(f"\n{report_type} raw response:")
                        pprint.pprint(raw)
                        print(f"{report_type} parsed total kWh: {total}")
                    except Exception as exc:
                        print(f"{report_type} failed: {exc}")

                params = await cloud.get_device_params(device)
                print("\nDevice params:")
                pprint.pprint(params)

            print("")

    asyncio.run(main())
