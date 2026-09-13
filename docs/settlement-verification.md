# Domestic daily settlement verification

Reviewed 2026-09-13. Scope: the 45 domestic station/product mappings in
`config/settlement.json`; not international, hourly, or weekly contracts.

The current KXHIGHNY and KXRAIN series metadata identify The Weather Company
and https://weather.com/kalshi as the settlement provider. Current individual
market rules specify the station, date, and provider. The configured provider
has therefore been preserved rather than replaced with a generic NWS rule.

The domestic **Official Climate Reports** view on the provider's public page
identifies NWS/NOAA CLI products, with CF6 as backup, as its data source. Its
separate international observations view uses local calendar days; that method
must not be substituted for the domestic CLI product.

Evidence inspected:

- https://weather.com/kalshi (domestic climate view, source note)
- Provider page asset containing that rendered source note at review time:
  https://weather.com/vc-ap-7f3f87/_next/static/chunks/0a0fdwrt6a1n8.js
- https://www.weather.gov/lot/weather_observations_faq
  defines the CLI calendar day as midnight to midnight local standard time,
  including the temperature and precipitation statistics.
- https://help.kalshi.com/en/articles/13823837-weather-markets
  explains local standard time for daily climate reports and the primacy of
  the source named in each individual contract.
- https://api.elections.kalshi.com/trade-api/v2/series/KXHIGHNY
- https://api.elections.kalshi.com/trade-api/v2/series/KXRAIN

The registry records this evidence and verifies the domestic reporting
convention. Each build and quote refresh independently checks the current
contract station, provider, rain threshold, and closing boundary. A changed or
missing definition blocks eligibility. The closing boundary is a consistency
check, not the sole evidence for a provider's reporting method.

Example: NYC on September 13, 2026 uses September 13 05:00 UTC to September 14
05:00 UTC. The current contract closes at the latter boundary. A change to
04:00 UTC would fail the check and require a new review.
