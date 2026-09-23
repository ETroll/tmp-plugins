---
name: weather-lookup
description: Look up current weather and short-term forecasts using this plugin's bundled weather-server MCP tool. Use when the user asks about current weather or a forecast for a location.
---

# Weather lookup

This plugin bundles an MCP server (`weather-server`, declared in `mcp.json`) that
exposes weather tools over stdio.

1. Call the `weather-server` MCP tools for current conditions or forecasts,
   passing the location the user asked about.
2. If no location is given, fall back to the default in `config/weather.json`
   (`defaultLocation`).
3. Summarize the result in the units configured there (`units`).

If the MCP server is unavailable, say so explicitly rather than guessing at
weather data.
