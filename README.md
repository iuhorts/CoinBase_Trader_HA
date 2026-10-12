# CoinBase_Trader_HA

Bot de grid trading per **Coinbase Advanced Trade** dissenyat per córrer dins **Home Assistant OS (Raspberry Pi 5)** com a add-on local, amb sensors a Home Assistant i alertes via **Telegram**.

AVÍS DE RISC: operar criptomonedes té risc de pèrdua total del capital. Aquest bot NOMÉS fa servir ordres **limit post-only (maker)** per pagar les comissions més baixes, i **rebutja automàticament qualsevol configuració de reixa que no sigui rendible després de comissions**.

## Què fa

- Reixa (grid) d'ordres **maker** al voltant d'un preu ancorat, per defecte `ETH-EUR`.
- Espaiat de reixa validat contra les **comissions reals** (les llegeix de l'API si tens keys).
- Filtre de tendència amb **EMA + RSI**: no compra en caigudes fortes ni amb RSI sobrecomprat.
- Cada loop: detecta fills, reconcilia ordres obertes, reomple la graella, actualitza sensors de HA i notifica per Telegram.
- Mode **dry-run** per provar sense diners.
- Ordres per Telegram: `/status`, `/grid`, `/pause`, `/resume`, `/stop`.

## Com està fet

```
CoinBase_Trader_HA/
├── repository.json            # per afegir-lo com a repositori d'add-ons a HA
├── coinbase_trader/           # l'add-on
│   ├── config.yaml            # opcions + schema de l'add-on
│   ├── Dockerfile             # Python 3.12-slim amb coinbase-advanced-py
│   ├── requirements.txt
│   └── app/
│       ├── main.py            # bucle principal + sensors
│       ├── settings.py        # opcions /data/options.json + env
│       ├── exchange.py        # wrap de la Coinbase Advanced API
│       ├── strategy.py        # grid + filtre EMA/RSI + viabilitat de fees
│       ├── indicators.py      # EMA, SMA, RSI
│       ├── ha.py              # API del supervisor (sensors)
│       └── notifier.py        # Telegram
└── docs/INSTALL_HA.md         # guia d'instal·lació pas a pas
```

## Començar ràpid (desenvolupament local)

```bash
python -m venv .venv
source .venv/bin/activate         # a Windows: .venv\Scripts\activate
pip install -r coinbase_trader/requirements.txt

# Prova sense keys ni diners (només dades de mercat)
$env:DRY_RUN = "true"; $env:POLL_INTERVAL_SECONDS = "10"
python -m app.main                # dins de coinbase_trader/
```

## Instal·lació a Home Assistant OS

Segueix `docs/INSTALL_HA.md`. En resum:

1. Crea una **CDP API key** a https://portal.cdp.coinbase.com (permissos **View + Trade**, sense Withdraw).
2. Crea el **bot de Telegram** amb @BotFather i aconsegueix el `chat_id`.
3. A HA: Settings → Add-ons → Store → Repositories → afegeix `https://github.com/iuhorts/CoinBase_Trader_HA`.
4. Instal·la **Coinbase Trader**, posa les keys/opcions i engega'l.

Els sensors apareixeran com `sensor.coinbase_trader_*` (status, price, balance_quote, balance_base, realized_pnl, cycles, rsi, ema_fast, ema_slow, fee_maker, fee_taker, open_orders, holds, last_action, grid).

## Opcions principals (config.yaml de l'add-on)

| Opció | Defecte | Significat |
|---|---|---|
| `dry_run` | `false` | Tira sense ordres reals (simula) |
| `product_id` | `ETH-EUR` | Parell a operar (compte en EUR) |
| `grid_levels` | `8` | Nombre de nivells de la reixa |
| `grid_spacing_pct` | `2.0` | Espaiat entre nivells (%) |
| `grid_anchor_price` | `0` | Preu d'ancoratge (0 = preu actual al primer arrencada) |
| `min_profit_margin_pct` | `0.4` | Marge net mínim (%) per nivell |
| `maker_fee_pct` / `taker_fee_pct` | `0.6` / `1.2` | Fallback (el codi llegeix la fee real si pots) |
| `use_signals` | `true` | Filtre EMA/RSI actiu |
| `poll_interval_seconds` | `60` | Freqüència del loop |
| `reserve_quote_pct` | `0` | % del saldo EUR que no es toca |
| `telegram_enabled` / `telegram_commands` | `false` / `true` | Notificacions i comandes |

## Seguretat

- Les keys es guarden a les **opcions de l'add-on** (camp `password`), mai al repositori.
- La key CDP ha de tenir permissos **View + Trade**; **no** activis Withdraw/Transfer, i valora la **IP allowlist**.
- `0.60%`+`0.60%` de maker round-trip implica que una reixa amb espaiat inferior a ~1.6% **perd diners**: el bot ho bloqueja.