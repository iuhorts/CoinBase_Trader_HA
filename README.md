# CoinBase_Trader_HA

Bot de **grid trading multi-actiu** per **Coinbase Advanced Trade**, dissenyat per córrer dins **Home Assistant OS (Raspberry Pi 5)** com a add-on, amb sensors a Home Assistant i alertes per **Telegram**.

Fa servir els actius que ja tens a Coinbase: **ven** una part de cada cripto als nivells alts de la graella i les **recompra** més avall, sense necessitar gaire moneda fiduciària. També pot operar un parell fix si li indiques `products`.

AVÍS DE RISC: operar criptomonedes pot fer perdre tot el capital. El bot només fa ordres **limit post-only (maker)** per pagar les comissions més baixes, i **rebutja qualsevol reixa que no sigui rendible després de comissions**.

## Què fa

- Descobreix automàticament els actius amb valor suficient i els hi munta una **graella d'inventari**.
- **Ven** quotes de cada actiu per sobre del preu i **recompra** per sota (grid).
- Cost base per lots **FIFO**: el P/L realitzat mostra el guany real de cada cicle.
- Filtre de tendència **EMA + RSI**: no compra en caigudes fortes ni amb RSI sobrecomprat.
- Valida l'espaiat contra les **comissions reals** (les llegeix de l'API si tens keys).
- Sensors per actiu a HA (`sensor.coinbase_trader_<actiu>_*`) més un resum agregat.
- Comandes Telegram: `/status`, `/grid`, `/pause`, `/resume`, `/stop`.
- Mode **dry-run** per provar sense diners.

## Estructura

```
CoinBase_Trader_HA/
├── repository.json            # repositori d'add-ons per a HA
├── coinbase_trader/           # l'add-on
│   ├── config.yaml            # opcions + schema
│   ├── Dockerfile             # Python 3.12-slim + coinbase-advanced-py
│   ├── requirements.txt
│   └── app/
│       ├── main.py            # orquestrador multi-actiu + sensors
│       ├── settings.py        # opcions (/data/options.json) + env
│       ├── exchange.py        # wrap Coinbase Advanced API
│       ├── discovery.py       # tria d'actius segons saldos
│       ├── grid.py            # graella + lots FIFO per actiu
│       ├── strategy.py        # grid + filtre EMA/RSI + viabilitat de fees
│       ├── indicators.py      # EMA, SMA, RSI
│       ├── ha.py              # API del supervisor (sensors)
│       └── notifier.py        # Telegram
└── docs/INSTALL_HA.md         # instal·lació pas a pas
```

## Començar ràpid (desenvolupament local)

```bash
python -m venv .venv
source .venv/bin/activate         # Windows: .venv\Scripts\activate
pip install -r coinbase_trader/requirements.txt

# Prova sense keys ni diners
$env:DRY_RUN = "true"
$env:PRODUCTS = "AAVE-USD,ADA-USD,HBAR-USD"
$env:DRY_RUN_BASE_BALANCE = "10"
python -m app.main                # dins de coinbase_trader/
```

## Instal·lació a Home Assistant OS

Veure `docs/INSTALL_HA.md`. Resum:

1. Crea una **CDP API key** a https://portal.cdp.coinbase.com (View + Trade, sense Withdraw).
2. Crea el **bot de Telegram** amb @BotFather i el `chat_id`.
3. A HA: Settings → Add-ons → Store → Repositories → afegeix `https://github.com/iuhorts/CoinBase_Trader_HA` → instal·la **Coinbase Trader**.

## Opcions principals

| Opció | Defecte | Significat |
|---|---|---|
| `dry_run` | `false` | Tira sense ordres reals (simula) |
| `products` | `[]` | Llista manual de parells; buit = autodescoberta |
| `min_product_value` | `10` | Valor mínim per incloure un actiu |
| `quote_currency_preference` | `["USDC","EUR","USDT","USD"]` | Ordre de preferència de moneda de cotització |
| `grid_levels` | `8` | Nombre de nivells de la graella |
| `grid_spacing_pct` | `2.0` | Espaiat entre nivells (%) |
| `grid_anchor_price` | `0` | Ancoratge fix; `0` = preu actual al primer arrencada |
| `min_profit_margin_pct` | `0.4` | Marge net mínim (%) per nivell |
| `maker_fee_pct` / `taker_fee_pct` | `0.25` / `0.5` | Fallback (es llegeix la fee real si pot) |
| `reserve_base_pct` | `25` | % de cada actiu que NO es posa a la venda |
| `use_signals` | `true` | Filtre EMA/RSI actiu |
| `poll_interval_seconds` | `60` | Freqüència del loop |
| `max_open_orders` | `12` | Màxim d'ordres obertes per actiu |
| `telegram_enabled` / `telegram_commands` | `false` / `true` | Notificacions i comandes |

## Seguretat

- Les keys es guarden a les **opcions de l'add-on** (camp `password`), mai al repositori.
- Permisos de la key: **View + Trade**; **no** Withdraw/Transfer. Valora la **IP allowlist**.
- Com que l'espaiat ha de superar `2 × maker_fee` + marge, amb fee maker 0,25% cal espaiat > ~0,9%: el bot bloqueja rangs no rendibles.
