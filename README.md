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

Miele@LAN brings your Miele@home appliances into Home Assistant over your home network.
Home Assistant talks to each appliance directly. There is no cloud service in between and no extra server to run.
The Miele app keeps working alongside it.

<!-- ha-marketplace:exclude-start -->
**Contents:**
[What you get](#what-you-get) ·
[Supported appliances](#supported-appliances) ·
[Before you start](#before-you-start) ·
[Installation](#installation) ·
[Set up](#set-up) ·
[Using it](#using-it) ·
[Good to know](#good-to-know) ·
[Troubleshooting & FAQ](#troubleshooting--faq) ·
[Getting help](#getting-help)
<!-- ha-marketplace:exclude-end -->

## What you get

- **Local control.** Your appliances keep working in Home Assistant when the internet is down or Miele's cloud is slow.
- **Fast updates.** Appliances send changes to Home Assistant the moment they happen. Where that is not possible, Home Assistant asks for updates on a timer.
- **All appliances in one go.** One setup adds every appliance in your Miele household.
- **Useful sensors.** Programme, phase, time remaining, finish time, progress, temperatures, doors, and more.
- **Controls where the appliance allows them.** Start, stop, pause, power, light, hood fan and dishwasher programme choice.
- **Energy and water data** on some dishwashers and washing machines, ready for the Energy dashboard.
- **English and German** user interface.

## Supported appliances

Each appliance shows up as its own device in Home Assistant.
What you get depends on the appliance type, and sometimes on the model and its firmware (the software inside the appliance).

| Appliance | What you can see | What you can control |
|---|---|---|
| **Oven** (including steam, combi and microwave ovens) | Status, programme, phase, time remaining, time elapsed, start time, progress, finish time, cavity and core temperature, target temperatures, door, notifications | Stop program, Power, Light, Wake |
| **Hob** | Status, per cooking zone: power level, residual heat, timer, pot detected. Cooktop timer | — (view only) ¹ |
| **Dishwasher** | Status, programme, phase, times, progress, finish time, door, notifications. On some models: AutoDos PowerDisk, salt and rinse aid levels, energy and water use ² | Start, Stop (remote), Pause, Resume, Power, Wake, Programme ³ |
| **Washing machine, tumble dryer, washer-dryer** | Status, programme, phase, times, progress, finish time, door, notifications, target temperature, spin speed, drying step, Smart grid. On some models: TwinDos fill levels and dosage, energy and water use ² | Start, Stop (remote), Wake ⁴ |
| **Fridge, freezer, fridge-freezer** | Temperature and target temperature per zone, door per zone, SuperCool, SuperFreeze | — (view only) ⁵ |
| **Wine cabinet** | Temperature and target temperature per zone, door per zone | — (view only) ⁵ |
| **Hood** | Status, fan level, filter saturation (some models) | Ventilation fan, Fan run-on time and Light, **or** Power and Light ⁶ |
| **Coffee system** | Status | Power, Light, Wake |
| **Dish warmer** | Status, door | Power, Wake |

**Wake** and **Stop (remote)** are explained under [Using it](#using-it). **Smart grid** shows whether the machine's SmartGrid setting is on.

Every appliance also gets a **Failure** sensor and a few **diagnostic** sensors (Wi-Fi signal, push status, remote control setting, and more).
Some diagnostic sensors are turned off by default. To turn one on, open the device page, show the hidden entities, select the sensor, open its settings and switch on **Enabled**.

**Notes**

1. The KMDA7876 hob with built-in extractor also shows **Extractor speed** (Off, 1–3, Boost) and a read-only **Extractor** fan. You cannot change the extractor from Home Assistant.
2. These values only exist where the appliance's firmware shares them. They show up on their own after setup, as soon as the appliance first answers, so they can take a while to appear. Lifetime energy and water totals have been seen on G7655, G7197 and G7650 dishwashers and the WEJ885 washing machine. Per-programme energy and water use exist for the WMV960 washing machine and the G6992 dishwasher.
3. The **Programme** list appears only on dishwashers that share their programme list (for example a G7655). See [Choosing a dishwasher programme](#choosing-a-dishwasher-programme).
4. Washing machines and dryers have no Power switch.
5. Miele does not allow changing settings on fridges, freezers and wine cabinets over the home network. You can watch them, but not change temperatures or switch SuperCool or SuperFreeze. Changes you make in the Miele app still show up in Home Assistant right away. Wine cabinets that report their interior light also get a **Light** entity. Whether switching it works has not been confirmed yet.
6. Hoods come in two kinds, depending on the hood's protocol. One kind gets a **Ventilation** fan (off, Level 1–3, Boost), a **Fan run-on time** setting (Off, 5 or 15 minutes) and filter saturation sensors. The other kind gets a **Power** switch and a read-only **Ventilation step** sensor. **Fan run-on time** shows the last value set from Home Assistant; the hood does not report it.

## Before you start

Check these points first. Most setup problems come from one of them.

- [ ] **Home Assistant 2024.12 or newer.**
- [ ] **Your appliances are connected to your Wi-Fi** and work in the Miele app.
  If an appliance has never been connected, set it up in the Miele app first. That is the easiest way.
- [ ] **Home Assistant and the appliances are on the same network.**
  Appliances announce themselves on the local network (this is called mDNS or "multicast"). Home Assistant must be able to hear those announcements.
  Separate networks for smart devices (VLANs), some mesh Wi-Fi systems and Docker setups can block them. See [Appliance not found](#appliance-not-found-or-setup-fails) under Troubleshooting if this applies to you.
- [ ] **Remote control is allowed on the appliance.**
  Controls only work when the appliance allows them. On the appliance's own settings menu, switch on **Remote control** (also called **Mobile controllable**). To start washing machines and dryers from Home Assistant, also switch on **Mobile Start** on the appliance.
- [ ] **You know how you will get your household key.**
  Every Miele household has a secret key. Home Assistant needs it to talk to your appliances. You have three options:
  - **Log in with your Miele account (recommended).** Home Assistant fetches the key from Miele for you. You need a desktop computer with Chrome, Edge or Firefox for one step.
  - **Paste tokens** you already have from an earlier Miele login. For advanced users.
  - **Paste the GroupID and GroupKey** if you already have them. Nothing is sent to Miele.

<!-- ha-marketplace:exclude-start -->
## Installation

### Home Assistant 2026.11 and newer (Marketplace)

The Marketplace replaced HACS in Home Assistant 2026.11. Miele@LAN is not in the Marketplace catalog yet, so you add it from a link.

**One click:** select this button, then confirm in your Home Assistant.

[![Open Marketplace repository on My Home Assistant](https://my.home-assistant.io/badges/marketplace_repository.svg)](https://my.home-assistant.io/redirect/marketplace_repository/?owner=tiehfood&repository=ha-miele-at-lan&category=integration)

**Or by hand:**

1. In Home Assistant, go to **Settings** > **Marketplace**.
   The first time, you see a notice called "Before you install from the Marketplace". Read it, tick **I understand the risks and agree**, and select **Continue**.
2. In the upper-right corner, select **Add from link**.
3. Under **GitHub link**, enter `https://github.com/tiehfood/ha-miele-at-lan`.
4. Select **Add**. If Home Assistant asks for the type, choose **Integration** and select **Add** again.
   Adding a repository from a link needs a GitHub connection. If you have not connected GitHub yet, Home Assistant asks you to do that first.
5. Open the Miele@LAN page and select **Install**. Check the version in the dialog and select **Install** again.
6. On a first install, setup usually opens on its own and asks you to pick a setup method. Go on with [Step 2](#step-2-choose-how-to-get-your-household-key) under Set up.
   If Home Assistant says it needs a restart first, select **Restart Home Assistant**. Afterwards, open Miele@LAN in the Marketplace again and select **Set up**.
   If you close setup, you can start it later under **Settings** > **Devices & services**.

If you used HACS before upgrading to 2026.11, your Miele@LAN install moved to the Marketplace automatically. You do not need to do anything.

### Home Assistant 2026.10 and older (HACS)

1. Open **HACS** in the sidebar.
2. Select the three dots in the upper-right corner, then **Custom repositories**.
3. Enter `https://github.com/tiehfood/ha-miele-at-lan`, choose the type **Integration**, and select **Add**.
4. Open Miele@LAN in HACS and select **Download**.
5. Restart Home Assistant.
6. After the restart, go on with [Set up](#set-up).

### Manual installation

1. Download **Source code (zip)** of the latest release from the [releases page](https://github.com/tiehfood/ha-miele-at-lan/releases/latest).
2. Unzip it and copy the folder `custom_components/miele_lan/` into your Home Assistant configuration folder (the folder that holds `configuration.yaml`).
   The result must be `custom_components/miele_lan/` next to `configuration.yaml`.
3. Restart Home Assistant.
4. After the restart, go on with [Set up](#set-up).
<!-- ha-marketplace:exclude-end -->

## Set up

### Step 1: Add the integration

[![Start setup of Miele@LAN on My Home Assistant](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=miele_lan)

Or by hand:

1. Go to **Settings** > **Devices & services**.
2. Select **Add integration** and search for **Miele@LAN**.
3. Home Assistant asks you to **pick a setup method**. Go on with Step 2.

You only do this once. One setup covers every appliance in your Miele household.

### Step 2: Choose how to get your household key

Most people choose Option A.

#### Option A: Cloud pairing (recommended)

This option uses your Miele account to fetch your household key. All control and updates then stay on your home network. Home Assistant only contacts Miele from time to time to keep the login fresh.
Do this step on a **desktop computer with Chrome or Edge** (Firefox works too).

1. Choose **Cloud pairing (recommended — uses your Miele account, doesn't break the Miele app)**.
2. Pick the **Country** your Miele account is registered in, and select **Submit**.
   This is the country you chose when you created the account. It is not always the country you live in now. [My country is not in the list](#my-country-is-not-in-the-list) under Troubleshooting lists the 49 choices.
3. Home Assistant shows a link called **Open the authorization URL**. Before you open it:
   1. Open the browser's developer tools: **F12** on Windows and Linux, **Cmd+Option+I** on a Mac.
   2. Select the **Network** tab.
   3. Make sure the list is not cleared when the page changes. Chrome and Edge: tick **Preserve log**. Firefox: open the gear icon in the Network tab and tick **Persist Logs**.
4. Open the link and log in with your Miele account.
5. At the end, the browser refuses to open a link that starts with `miele://`. **That is expected.**
6. In the **Network** tab, find the **last entry whose address starts with `miele://`**. It is usually shown in red as blocked.
   To find it faster, type `oauth2-code` into the filter box of the Network tab.
7. Right-click that entry and copy its address. Chrome and Edge: **Copy** > **Copy URL** (**Copy link address** in some versions). Firefox: **Copy Value** > **Copy URL**.
8. Go back to Home Assistant. Paste the whole `miele://oauth2-code/?code=…&state=…` address into the field and select **Submit**.

**If it fails:**

- "The pasted URL is not a valid `miele://oauth2-code/...` redirect": you copied the wrong entry, or one from an earlier attempt. Copy the last `miele://` entry from this attempt.
- "Cloud login failed": the code was already used or has expired. Start setup again and paste the address right after logging in.
- "Could not fetch your household GroupKey from Miele cloud": check that you picked the right country and that your account has appliances in the Miele app. Otherwise use Option C.

**What happens next:** Home Assistant fetches your household key, finds your appliances on the network and adds them.
After a short moment you see each appliance as a device under **Settings** > **Devices & services** > **Miele@LAN**.
Home Assistant keeps your Miele login fresh in the background.

#### Option B: Paste pre-obtained tokens

For advanced users who already have a Miele **refresh token** and **access token** from an earlier Miele login (for example from another Home Assistant install).

1. Choose **Cloud pairing — paste pre-obtained tokens**.
2. Pick your **Country**, paste the **Refresh token** and the **Access token**, and select **Submit**.

What happens next is the same as in Option A.

#### Option C: Paste GroupID + GroupKey

Use this if you already have your household's **GroupID** (16 hexadecimal characters) and **GroupKey** (128 hexadecimal characters). Nothing is sent to Miele, so it works no matter which country your account is in.

1. Choose **Paste GroupID + GroupKey (for households whose key you already have)**.
2. Paste the **GroupID** and the **GroupKey**, and select **Submit**.

**What happens next:** Home Assistant looks for appliances on your network that belong to this household and adds them.
This option needs the appliance announcements (mDNS) to reach Home Assistant during setup. If none arrive, you see "Host reachable but no devices reported." See [Appliance not found](#appliance-not-found-or-setup-fails) under Troubleshooting.

### After setup

- **Values and entities fill in over time.** Many sensors show "Unknown" until the appliance is switched on. Consumable levels, energy totals and the dishwasher programme list are added once the appliance first answers. See [A sensor stays "Unknown" or empty](#a-sensor-stays-unknown-or-empty) under Troubleshooting.
- **Added a new appliance to your household later?** Go to **Settings** > **Devices & services** > **Miele@LAN**, select the three dots and choose **Reload**. The new appliance is picked up.
- **Something missing?** See [One appliance is missing](#one-appliance-is-missing-the-others-work) under Troubleshooting.

### Optional: Configure

Most people never need this. Go to **Settings** > **Devices & services** > **Miele@LAN** and select **Configure**.

- **Static IP addresses.** Only needed if your network blocks appliance announcements. Give the appliance a fixed address in your router first, or it may change. Then enter that IPv4 address in the field labelled with the appliance's fab number (its serial number); the form lists each appliance's name next to its number. Leave a field empty to keep automatic discovery.
  If no appliances are listed yet, the form shows one text box instead. Enter one line per appliance as `fabnumber=IP address`. The fab number has 12 digits.
- **Advertise address.** The IPv4 address appliances should use to reach Home Assistant. Only needed if Home Assistant runs behind a reverse proxy or in an isolated network. Leave it empty to detect it automatically. Never enter an appliance's address here.

Saving reloads the integration.

## Using it

### Start, stop and pause

Use the buttons on the appliance's device page.

- **Dishwasher:** **Start**, **Stop (remote)**, **Pause** and **Resume**.
- **Washing machine and dryer:** **Start** and **Stop (remote)**.
  Start works when the machine is **Waiting to start**, or when it is **Programmed** and **Mobile Start** is switched on at the machine with no delayed start set.
  Stop works while a programme is waiting to start or running.
- **Oven:** **Stop program**.
- **Power:** ovens, dishwashers, coffee systems, dish warmers and some hoods have a **Power** switch.
  If the appliance is in deep standby, Home Assistant wakes it and tries again. A switch can then take a few seconds.
- **Wake:** wakes the appliance from deep standby so it accepts commands. On washing machines and dryers this also switches the display on.

You always choose the programme on the appliance itself, or in the Miele app. The only exception is the dishwasher programme list below.

### Choosing a dishwasher programme

Dishwashers that share their programme list get a **Programme** selector.

1. Switch the dishwasher on.
2. Pick a programme in the **Programme** selector.
3. Press **Start** when you are ready. Choosing a programme never starts it.

You can only choose a programme while the dishwasher is on and not running.

### Energy dashboard

Some appliances report how much energy and water they use:

- **Lifetime energy consumption** and **Lifetime water consumption**: dishwashers and washing machines that share this data (seen on G7655, G7197 and G7650 dishwashers and the WEJ885 washing machine). A washing machine is not woken to read them, so they update while it is on.
- **Energy consumption** and **Water consumption** per programme: WMV960 washing machine and G6992 dishwasher.

To add them, go to **Settings** > **Dashboards** > **Energy**. Add the energy sensor under **Individual devices** and the water sensor under **Water consumption**.

Miele@LAN has no energy sensor for tumble dryers. Use a smart plug that measures power instead.

### Notifications and automations

Useful entities for automations:

- **Status**, for example **Program ended**, to get a message when the laundry is done.
- **Running**, which is on while a programme runs.
- **Finish time** and **Remaining**, for "ready at" messages.
- **Door**, for example to remind you to unload the dishwasher.
- **Failure** and **Notification**, which turn on when the appliance reports a problem or a message.

Create automations as usual under **Settings** > **Automations & scenes**.

## Good to know

- **How fast updates arrive.** Most appliances send changes to Home Assistant straight away. Appliances that cannot do that are checked every 10 seconds while a programme runs and every 60 seconds otherwise.
- **Washing machines and dryers are never woken** by Home Assistant's background checks.
  To read consumable levels, Home Assistant may wake a dishwasher's network module. If your dishwasher switches itself on, please [open an issue](https://github.com/tiehfood/ha-miele-at-lan/issues).
- **One household per Home Assistant.** Miele@LAN supports one Miele household per Home Assistant install.
- **No firmware updates.** Miele@LAN does not update the appliance's firmware.
- **Moving from the official Miele integration?** Entity names are different, so check your automations and dashboards after the switch.
- **Removing Miele@LAN** tries to remove Home Assistant's registration from the appliances whose IP address it has stored (set up with GroupID + GroupKey, or with static IP addresses). Leftover registrations are cleared automatically the next time Miele@LAN is set up.

## Troubleshooting & FAQ

### Appliance not found or setup fails

You see "no Miele devices reachable on the LAN yet", or the integration shows **Retrying setup**.
That means Home Assistant does not hear the appliances' announcements (mDNS). Home Assistant keeps retrying on its own.

Check:

1. **Same network.** Home Assistant and the appliances must be on the same network segment.
   - Separate VLANs or guest networks block the announcements. If you use them, ask your router to forward mDNS between them (an "mDNS reflector" or "mDNS repeater").
   - Some managed switches (IGMP snooping), access points and mesh systems filter the announcements.
   - In Docker, run Home Assistant with host networking (`network_mode: host`).
2. **Look at what Home Assistant hears.** Go to **Settings** > **System** > **Network** and open the Zeroconf browser. Search for `_mieleathome._tcp`.
   An entry starting with "HomeAssistant" is Miele@LAN itself, not an appliance.
   If that is the only entry, the appliance announcements do not reach Home Assistant: fix the network (point 1) or use static IP addresses. If your appliances are listed, reload the integration.
3. **Restart the appliance's network module.** Some appliances only start announcing themselves after they are unplugged and plugged back in.
4. **Weak Wi-Fi.** An appliance with a weak signal can miss the short search. Reload the integration to try again.

If you cannot fix the network, enter static IP addresses under [Configure](#optional-configure). Updates may then come in by polling only, not instantly.

### One appliance is missing, the others work

1. Look under **Settings** > **System** > **Repairs** for "Miele appliance(s) could not be enrolled". It names each appliance by its fab (serial) number and gives the reason.
2. **"no LAN IP found"**: Home Assistant cannot find the appliance on the network. See [Appliance not found](#appliance-not-found-or-setup-fails), or enter its IP address under [Configure](#optional-configure).
3. **"signed verify failed"**: the appliance is offline, or it is in a different household.
4. **"not commissioned into any household"**: the appliance was never fully added to your Miele household. On the appliance, open the settings menu, go to **Miele@home**, reset it, and add it again in the Miele app. Then reload Miele@LAN.
5. **"belongs to household …, but this entry is …"**: the appliance is in a different Miele household. See the next question.

If the missing appliance is your only one, there is no Repairs entry. Check the log instead (see [Getting help](#getting-help)).

### Miele@LAN shows "Failed to set up"

Check the log (see [Getting help](#getting-help)).

**"household … has no appliances, but this LAN has Miele appliance(s) in household …"**: your appliances belong to a different Miele household than the one your account holds now. This can happen after reinstalling the Miele app or pairing again. Fix it in one of two ways:

- Pair the appliances again in the Miele app, so they join your current household. Then reload Miele@LAN.
- Or use **Paste GroupID + GroupKey** (the log calls it "Paste household credentials") with the key of the household the appliances belong to.

**"household … has no appliances — … no Miele appliance answered mDNS on this LAN at all"**: this is a network problem. See [Appliance not found](#appliance-not-found-or-setup-fails).

**"could not bind push listener on port 18082"**: another program on the Home Assistant host already uses port 18082. Miele@LAN needs that port.

### My country is not in the list

Pick the country your Miele **account** is registered in. 49 countries are supported:

```
ae at au be bg br ca ch cl cn cy cz de dk ee es fi fr gb gr hk hr hu ie in it jp kr
lt lu lv mx my nl no nz pl pt ro rs se sg si sk th tr ua us za
```

If yours is missing, use **Paste GroupID + GroupKey**. It does not depend on the country.

### A button or switch does not work

**Start, Stop (remote), Pause and Resume:**

- **"Remote control is off on this appliance"**: switch on **Remote control** in the appliance's settings menu. The **Remote control** sensor shows the current setting.
- **"Start is not available right now"** on a washing machine or dryer: Start works when the machine shows **Waiting to start**, or when a programme is set, **Mobile Start** is on and no delayed start is set. Switch on Mobile Start on the machine; the **Mobile start** sensor turns on when it is active. If no programme is chosen yet, choose one on the machine first.
- **"Stop is not available right now"**: Stop only works while a programme is waiting to start or running.
- **"The appliance refused this command (HTTP …)"**: the appliance did not accept the command in its current state. Check the points above.

**Power, Stop program and Programme:**

- **"Remote control was refused"**: switch on **Remote control** / **Mobile controllable** in the appliance's settings menu.
- **"This appliance's firmware does not accept remote commands over the local API"**: some firmware blocks the Power switch and the programme choice over the home network. There is nothing to fix in Home Assistant. Start, Stop, Wake and Light can still work on these appliances.
- **"The appliance rejected the command (HTTP 500)"**: the appliance cannot carry out this command right now, or this model does not support it over the home network.

### The Power switch takes a few seconds

If the appliance is in deep standby, Home Assistant wakes it and tries again, up to twice. One press is enough. It usually takes under 10 seconds.

### A sensor stays "Unknown" or empty

- **Remaining, Elapsed, Start in and the programme:** these are hidden while the appliance is off, switched on without a programme, or idle. They show while a programme is set, running, paused or just finished. This matches the Miele app.
- **Right after setup:** many values only appear once the appliance is switched on.
- **Consumables, energy totals, programme list:** these appear after the appliance first answers. Switch the appliance on and wait up to about 10 minutes. If they still do not appear, reload Miele@LAN while the appliance is on.
- **Still "Unknown" while the appliance is running?** That is not expected. Please [open an issue](https://github.com/tiehfood/ha-miele-at-lan/issues) with your model.

### What does "Push status" mean?

**Push status** is a diagnostic sensor on each appliance. It shows how the appliance sends updates:

| Value | Meaning |
|---|---|
| **Push active** | Push works. The appliance has sent updates. |
| **Push ready, no update yet** | Push is set up. No update has arrived yet. |
| **Basic push, no update yet** | The appliance supports only the basic way of sending updates. This is normal for some models. No update has arrived yet. |
| **Polling only** | No push. Home Assistant asks the appliance on a timer. |

If push never arrives, the appliances may not be able to reach Home Assistant. They connect on **TCP port 18082**, so a firewall on the Home Assistant host must let incoming connections on that port through. Behind a reverse proxy or in an isolated network, set the **Advertise address** under [Configure](#optional-configure) and forward port 18082 to Home Assistant.

### How do I update Miele@LAN?

Updates show up like any other Home Assistant update. Install the update, then restart Home Assistant when asked.

### Will the Miele app keep working?

Yes. You can use the Miele app and Miele@LAN at the same time.

## Getting help

Open an issue on [GitHub](https://github.com/tiehfood/ha-miele-at-lan/issues). Please include your appliance model and your Home Assistant version.

**Before you post anything, remove** serial numbers (fab numbers), IP addresses, Wi-Fi network names, GroupIDs and GroupKeys. Never post your GroupKey: anyone who has it can control your appliances.

### Debug logs

The easiest way:

1. Go to **Settings** > **Devices & services** > **Miele@LAN**.
2. Select the three dots and choose **Enable debug logging**.
3. Do the thing that does not work.
4. Select the three dots again and choose **Disable debug logging**. Home Assistant downloads the log file.

Or add this to `configuration.yaml` and restart:

```yaml
logger:
  logs:
    custom_components.miele_lan: debug
    asyncmiele: debug
```

With debug logging on, each appliance's state appears in the log as lines containing `polled /State:` or `push /State content:`. These lines help the most.
Network captures do not help, because the traffic is encrypted.

### Read raw data from an appliance

The maintainer may ask you for raw data. Go to **Developer tools** > **Actions** and use one of these. Both only read; they never change anything on the appliance.

- `miele_lan.dump_rest_resource` (**Dump REST resource**): pick the device and a resource (`Ident`, `State` or `State.ExtendedState`).
- `miele_lan.dump_dop2_leaf` (**Dump DOP2 leaf**): pick the device and enter the unit and attribute numbers you were asked for.

There is also an experimental `miele_lan.select_program` action (**Select programme (experimental)**). It **does** change the selected programme on the appliance. Only use it when asked in an issue.

## For developers

The integration lives in `custom_components/miele_lan/`. The tests are in `tests/`.

```sh
git clone https://github.com/tiehfood/ha-miele-at-lan
cd ha-miele-at-lan
python3 -m venv virtenv && . virtenv/bin/activate
pip install homeassistant asyncmiele==0.2.6 pytest pytest-asyncio pyyaml
python -m pytest tests/
```

`tools/` contains two helpers that run from a repository checkout with the packages above installed:

- `tools/miele_lan_provision.py` sets up a factory-fresh appliance without the Miele app. It has the subcommands `wifi`, `commission`, `pair` and `verify`; run it with `--help` for the options. The keys are saved to `keys.yaml` in the repository root, which git ignores; pass `--out` (or `--keys` for `verify`) to use another file.
- `tools/dump_map_countries.py --check` compares the supported country list with Miele's service.

> **Warning:** the appliance does not check commissioning data. If it receives an empty or wrong key, it can lock itself to that key, and the real key is then refused. The only way back is on the appliance: settings menu > **Miele@home** > reset, then reconnect it in the Miele app. Use `tools/miele_lan_provision.py`, which checks key lengths before sending. Do not send keys by hand with `curl` or a script.

## Disclaimer

This project is independent and not affiliated with, endorsed by, or supported by Miele & Cie. KG. **Miele®** and **Miele@home®** are registered trademarks of Miele & Cie. KG; the appliance model names used here (such as KMDA, G and WMV) are likewise Miele trademarks used here purely for identification. Use of this software may void your appliance warranty — use at your own risk.

The brand icons at `custom_components/miele_lan/brand/` are the property of Miele & Cie. KG and are bundled here so Home Assistant can render the manufacturer logo on the device card. If a Miele representative would like them removed or replaced, please open an issue.

## Credits

Built on years of prior reverse-engineering. Thanks to:

- **[akappner](https://github.com/akappner)** — [`MieleRESTServer`](https://github.com/akappner/MieleRESTServer), [`asyncmiele`](https://pypi.org/project/asyncmiele/), and [`dop2rs`](https://github.com/akappner/dop2rs). Original public RE of `MieleH256`, DOP2, and the provisioning flow.
- **[schneidair](https://community.home-assistant.io/u/schneidair)** — independent discovery of the `MielePairing:Pairing` auth on EK057S.
- **[ajander](https://community.home-assistant.io/u/ajander)** — SpeedOven commissioning pcap.
- **[astrandb](https://github.com/astrandb)** + the HA core Miele maintainers — upstream cloud integration and [`pymiele`](https://pypi.org/project/pymiele/), used here for cross-reference.
- **HA Community thread [#840093](https://community.home-assistant.io/t/mielerestserver-miele-home-without-cloud-possible/840093)** contributors — for surfacing SuperVision, model quirks, and the OTA hosts.

If you find Miele@home material elsewhere that ought to be credited here, please open a PR.
