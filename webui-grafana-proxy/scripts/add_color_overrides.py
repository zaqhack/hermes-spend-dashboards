import json

PALETTE = {
    "bright": "#3DCCC4",
    "dantsu": "#50FF3A",
    "mayano": "#FA863F",
    "ripper": "#EAB308",
    "robroy": "#9AA5B1",
    "satono": "#2DA84F",
    "suzuka": "#FFFFFF",
}

def color_override_regexp(agent, hexcolor):
    # Match any series whose name starts with the agent tag (handles
    # "{{agent}} / {{model}}", "{{agent}} / {{session}}", "{{agent}} input", etc.)
    return {
        "matcher": {"id": "byRegexp", "options": f"^{agent}( |/|$)"},
        "properties": [
            {"id": "color", "value": {"mode": "fixed", "fixedColor": hexcolor}}
        ],
    }

def apply(dash, only_types=("timeseries", "barchart", "bargauge")):
    changed = 0
    for p in dash.get("panels", []):
        if p.get("type") not in only_types:
            continue
        targets = p.get("targets", [])
        qtext = json.dumps(targets)
        if "agent" not in qtext:
            continue
        fc = p.setdefault("fieldConfig", {})
        ovs = fc.setdefault("overrides", [])
        # Remove any of OUR prior byRegexp agent-color overrides (idempotent re-run)
        ovs[:] = [
            o for o in ovs
            if not (
                o.get("matcher", {}).get("id") == "byRegexp"
                and any(o["matcher"].get("options", "").startswith(f"^{a}") for a in PALETTE)
                and any(prop.get("id") == "color" for prop in o.get("properties", []))
            )
        ]
        for agent, hexcolor in sorted(PALETTE.items()):
            ovs.append(color_override_regexp(agent, hexcolor))
            changed += 1
    return changed

for uid in ["ai-usage-tracking", "ai-fleet-aggregate"]:
    path = f"/tmp/dash/{uid}.json"
    d = json.load(open(path))
    dash = d["dashboard"]
    n = apply(dash)
    json.dump(d, open(path, "w"), indent=2)
    print(f"{uid}: applied {n} agent-color byRegexp overrides")

print("done")
