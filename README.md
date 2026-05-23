<p align="center">
  <a href="https://my.home-assistant.io/redirect/hacs_repository/?owner=tiehfood&repository=ha-miele-at-lan&category=integration">
    <img alt="Open in HACS" src="https://my.home-assistant.io/badges/hacs_repository.svg"/>
  </a>
</p>
<p align="center">
  <a href="https://github.com/tiehfood/ha-miele-at-lan/releases/latest">
    <img alt="Latest release" src="https://img.shields.io/github/v/release/tiehfood/ha-miele-at-lan?label=release"/>
  </a>
  <a href="https://www.home-assistant.io">
    <img alt="HA 2024.12+" src="https://img.shields.io/badge/HA-2024.12%2B-blue.svg"/>
  </a>
  <a href="LICENSE">
    <img alt="License" src="https://img.shields.io/github/license/tiehfood/ha-miele-at-lan.svg"/>
  </a>
  <a href="https://www.buymeacoffee.com/tiehfood">
    <img alt="Buy Me A Coffee" src="https://raw.githubusercontent.com/pachadotdev/buymeacoffee-badges/main/bmc-orange.svg"/>
  </a>
</p>

# Miele@LAN

Local Home Assistant integration for Miele@home appliances — ovens, hobs, dishwashers, washers, dryers, fridges, freezers, hoods, coffee systems, dish warmers, wine cabinets. Talks directly to the appliance over the LAN. No cloud account is required to run, no sidecar server, no Miele 3rd-party API.

- Speaks Miele's local REST + DOP2 binary protocol — `MieleH256` HMAC-SHA256 + AES-CBC, signed with your household `GroupKey`
- Real-time state via Miele's own **SuperVision push** channel — sub-second updates with a low-rate polling fallback (1 s active, 30 s idle)
- mDNS auto-discovery (`_mieleathome._tcp`) — one config-flow click adds every appliance in the household
- One-shot factory-fresh **provisioning tool** included (`tools/miele_lan_provision.py`) — for households without an existing Miele app
- Optional Miele cloud OAuth pairing — fetches your household `GroupKey` once so the Miele app keeps working alongside HA
- Read-only by design for cooling appliances (fridge/freezer/wine cabinet — see [Limitations](#limitations))

## Supported appliances

| Family | Models | Sensors | Controls |
|---|---|---|---|
| **Oven** (incl. steam, combi, microwave) | H7560BP, H7164BP, DGC7860HCXL, DGM7440, … | status, program, phase, remaining/elapsed/start time, cavity & core temps, target & core-target, door, signals, light | start / stop / pause, wake, power, light, target temp* |
| **Hob** | KM7576, KM7895 FL induction, induction + extractor | per-zone power (1..12 incl. ½ steps, boost/boost+, keep-warm), per-zone residual heat, per-zone timer, status | — |
| **Dishwasher** | G7000-series + semi-pro/professional | status, program, phase, remaining/elapsed time, door, signals | start / stop / pause, wake, power |
| **Washer / dryer / washer-dryer** | WWG/TWC/WWV/WTV series | status, program, phase, drying step, remaining/elapsed/start time, door, signals | start / stop / pause, wake |
| **Fridge / freezer / fridge-freezer** | KF 7772 B, K 7000, KFN, KFNS, … | per-zone current + target temp, per-zone door, SuperCool, SuperFreeze, failure | — *(see Limitations)* |
| **Wine cabinet** | KWT 6000, KWNS, KWTUS, wine + freezer | per-zone temp, per-zone door, light state | — *(see Limitations)* |
| **Hood / range vent** | DA series | fan step, light state | light |
| **Coffee system** | CVA series | status, program, phase | start, stop, pause, wake, power |
| **Dish warmer** | ESW series | status | start, stop, wake, power |

Diagnostic entities (raw enums, WLAN info, push state, firmware version) are created but disabled by default — enable them per device under **Configure entities**.

\* Oven setpoint writes work where firmware honours `MobileStart` (RE'd on H7560BP). Some appliances may need MobileStart enabled on the panel.

## Installation

### HACS

[![Open in HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=tiehfood&repository=ha-miele-at-lan&category=integration)

Or manually: HACS → ⋮ → Custom repositories → add `https://github.com/tiehfood/ha-miele-at-lan` as **Integration** → install → restart HA.

### Manual

Copy `custom_components/miele_lan/` into your HA config (final path: `<config>/custom_components/miele_lan/`) and restart.

### Adding your household

**Settings → Devices & services → Add Integration → Miele@LAN.** Three setup paths:

- **Cloud pairing (recommended).** Log in once with your Miele account; HA fetches the household `GroupKey` from `rest-eu.domestic.miele-iot.com`, mDNS-discovers every appliance, and provisions a SuperVision push listener. The Miele app continues to work in parallel.
- **Paste pre-obtained tokens.** Skip the in-flow OAuth if you already have an `access_token` + `refresh_token` from a Miele OAuth flow against `prod.map.miele-iot.com` (e.g. captured during the Cloud-pairing step in another HA instance).
- **Paste household credentials.** If you've already extracted `GroupID` + `GroupKey` (e.g. from `MieleRESTServer`), paste them directly — no cloud round-trip needed.

For factory-fresh appliances, run `python tools/miele_lan_provision.py` on your laptop first to commission them — this writes a new `GroupID`/`GroupKey` and binds the appliance to your LAN.

### Capturing the OAuth redirect (Cloud pairing)

The cloud-pair flow opens a Miele login page that ends with a `miele://oauth2-code/?code=…&state=…` redirect. Browsers refuse to navigate the `miele://` scheme, so the URL never appears in the address bar — you have to grab it from the network log:

1. Open the authorization URL from the HA config-flow step in **Chrome / Edge on a desktop computer**.
2. Open **DevTools → Network** tab (F12) **before** logging in, and enable *Preserve log* so the redirect isn't cleared.
3. Log in with your Miele account.
4. The browser will refuse the final navigation — that's expected. In the Network tab, find the **last request whose URL starts with `miele://`** (usually highlighted red as "blocked").
5. Right-click that row → *Copy → Copy link address*.
6. Paste the full `miele://oauth2-code/?code=…&state=…` URL into the config-flow input.

Same flow works in Firefox (DevTools → Network → look for the blocked `miele://` redirect).

## Limitations

- **Fridges, freezers, and wine cabinets are read-only over LAN.** Miele's K7000/EasyControl firmware (XKM `EK057*`) does not expose any LAN write path for cooling appliances — verified end-to-end against a Miele KF 7772 B. Setpoint writes from the Miele app reach the appliance via the device-initiated TLS WebSocket to `rest-eu.domestic.miele-iot.com` and the LAN HTTP server uniformly returns HTTP 403 for every PUT/POST regardless of authentication. This is [confirmed by Miele's own documentation](https://www.miele.de/support/customer-assistance/app-1199/alle_kategorien/mobilecontrol_und_mobilestart-132299522827): on cooling appliances, MobileStart is permanently on and not togglable. HA still receives state changes in real time via SuperVision push, so the Miele app's setpoint changes show up immediately.
- **No firmware update trigger.** Read-only `/Update/`.
- **`Mobile Controllable` on the panel** gates writes on cycle appliances (oven, dishwasher, washer/dryer). Enable it once on the appliance display before HA writes will succeed.
- **One household per HA install.** Multiple Miele households need separate HA instances.

## Troubleshooting

**Appliance not discovered?** Make sure HA and the appliance are on the same VLAN — Miele mDNS won't cross routed boundaries. Check `Settings → Devices & services → Discover devices` shows `_mieleathome._tcp` entries.

**Push not firing (sensors only update every 30 s)?** Verify `Mobile Controllable` is on at the appliance, and that HA's listener port (default 18082) is reachable from the appliance subnet. Look for `push:active` in the diagnostic Push State sensor.

**`HTTP error 403` on writes?** The appliance's `RemoteEnable` flag for `MobileCtrl` is `0`. Enable Mobile Controllable on the panel. Fridges, freezers, and wine cabinets stay 403 by firmware design — see Limitations.

**Debug logs:**

```yaml
logger:
  logs:
    custom_components.miele_lan: debug
    asyncmiele: debug
```

## Development

```sh
git clone https://github.com/tiehfood/ha-miele-at-lan
cd miele
python3 -m venv virtenv && . virtenv/bin/activate
pip install -e .
python -m pytest tests/
```

Parser/protocol tests have no HA imports — they run on plain Python.

## Disclaimer

This project is independent and not affiliated with, endorsed by, or supported by Miele & Cie. KG. **Miele®** and **Miele@home®** are registered trademarks of Miele & Cie. KG; the appliance model names (KM, H, DGC, G, WWG, TWC, KF, KFN, KWT, KWNS, ESW, CVA, DA) are likewise Miele trademarks used here purely for identification. Use of this software may void your appliance warranty — use at your own risk.

The brand icons at `custom_components/miele_lan/brand/` are the property of Miele & Cie. KG and are bundled here so Home Assistant can render the manufacturer logo on the device card. If a Miele representative would like them removed or replaced, please open an issue.

## Credits

Built on years of prior reverse-engineering. Thanks to:

- **[akappner](https://github.com/akappner)** — [`MieleRESTServer`](https://github.com/akappner/MieleRESTServer), [`asyncmiele`](https://pypi.org/project/asyncmiele/), and [`dop2rs`](https://github.com/akappner/dop2rs). Original public RE of `MieleH256`, DOP2, and the provisioning flow.
- **[schneidair](https://community.home-assistant.io/u/schneidair)** — independent discovery of the `MielePairing:Pairing` auth on EK057S.
- **[ajander](https://community.home-assistant.io/u/ajander)** — SpeedOven commissioning pcap.
- **[astrandb](https://github.com/astrandb)** + the HA core Miele maintainers — upstream cloud integration and [`pymiele`](https://pypi.org/project/pymiele/), used here for cross-reference.
- **HA Community thread [#840093](https://community.home-assistant.io/t/mielerestserver-miele-home-without-cloud-possible/840093)** contributors — for surfacing SuperVision, model quirks, and the OTA hosts.

If you find Miele@home material elsewhere that ought to be credited here, please open a PR.
