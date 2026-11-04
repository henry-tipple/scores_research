# VPN routing: one gateway per market

```
            vpn-uk (172.30.1.0/24)                        internet (UK exit)
 ┌──────────────┐   default route   ┌─────────────────┐   WireGuard/OpenVPN
 │ uk-c4-fotmob │ ────────────────▶ │ vpn-gw-uk  (.2) │ ───────────────────▶
 │   (.10+)     │                   └─────────────────┘
 │              │ ── direct ──────▶ ingest (.3)   (local subnet, never via VPN)
 └──────────────┘
   ADB: 127.0.0.1:56xx on the host (published port, so every instance keeps its own)
```

* `fleet/fleet/generate.py docker` prints the whole sequence: networks, gateways,
  ingest attachment, ReDroid containers, route fix-ups.
* Gateways are [gluetun](https://github.com/qdm12/gluetun) by default
  (`vpn.gateway_image` in `fleet.yaml`). Provider credentials live in
  `/etc/study/vpn/vpn-<market>.env` **on the server only**, never in git.
  `FIREWALL_OUTBOUND_SUBNETS` lets gateway and instances talk on the local subnet.
* The networks are normal bridges (not `--internal`) so ADB ports can be published.
  That means an instance would leak through the host's own uplink if its route
  fix-up failed. Add a kill-switch on the server:

  ```bash
  # drop anything from a market subnet that is not going to its gateway or ingest
  iptables -I DOCKER-USER -s 172.30.1.0/24 ! -d 172.30.1.0/24 -j DROP
  ```

  Gluetun NATs the instances' traffic out of its tunnel, so the gateway's own
  egress (from its tunnel interface) is unaffected.

## Needs validating on the real server

1. ReDroid uses Android's netd, which does policy routing per interface table.
   `ip route replace default via <gw>` in the main table may not be enough; the
   rule may need to go into table `eth0` (`ip route replace default via <gw> table eth0`)
   or be set through `ndc`. Test with `adb shell curl -s https://ifconfig.io/country_code`.
2. DNS: instances get `net.dns1=<gateway>`. Gluetun serves DNS on the gateway
   (DoT upstream), which stops DNS leaking outside the market.
3. The health check (`ops/ops/health.py`) checks the exit country through the
   gateway's control server. The command is in `.env` (`VPN_CHECK_CMD`).
