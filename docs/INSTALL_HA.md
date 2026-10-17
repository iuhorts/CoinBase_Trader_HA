# Instal·lació a Home Assistant OS (RPi5)

## 0. Requisits
- Home Assistant OS de 64 bits (RPi5) amb connexió a internet.
- Compte Coinbase amb cripto. Als comptes europeus **cal cotitzar en `USDC` (o `EUR`)**: els parells `-USD` estan restringits i donen l'error `account is not available`.
- No cal MQTT: el bot publica sensors directament via l'API del supervisor.

## 1. Crear la CDP API key (Coinbase Advanced Trade)

La API és **gratuïta**; no cal pagar "Coinbase One" ni cap subscripció.

1. Entra a https://portal.cdp.coinbase.com i inicia sessió amb el teu compte.
2. Crea un **Projecte**, i dins seu una **Secret API Key**.
3. Permisos: **View** + **Trade**. **NO** activis Transfer/Withdraw.
4. (Recomanat) Configura **IP allowlist** amb la IP pública de casa.
5. La clau es mostra **una sola vegada**:
```json
{
  "name": "organizations/0b9e.../apiKeys/...",
  "privateKey": "-----BEGIN EC PRIVATE KEY-----\n...\n-----END EC PRIVATE KEY-----\n"
}
```
Guarda el `name` i el `privateKey` sencer (incloent les línies BEGIN/END).

Alternativa si prefereixes keys clàssiques: https://www.coinbase.com/settings/api → API keys → *Advanced Trade*, permisos View+Trade (format `api_key` / `api_secret`).

## 2. Crear el bot de Telegram

1. Obre Telegram, busca **@BotFather**, envia `/newbot`.
2. Nom: `Coinbase Trader`, username que acabi en `bot` (ex. `iu_coin_trader_bot`).
3. Guarda el **token** que et dona.
4. Obre el teu bot i prem **Start**.
5. Per obtenir el **chat_id**: busca **@userinfobot** i envia-li qualsevol cosa → et dona el teu `Id`.

## 3. Afegir el repositori d'add-ons

1. A HA: **Settings → Add-ons → Add-on store**.
2. Botó amb tres puntets (⋮) a dalt a la dreta → **Repositories** / **Repositories**.
3. Afegeix: `https://github.com/iuhorts/CoinBase_Trader_HA`
4. Apareixerà l'add-on **Coinbase Trader** → **Install**.

## 4. Configurar l'add-on

A la pestanya **Configuration** omple:

| Camp | Valor |
|---|---|
| `dry_run` | `true` per provar primer; `false` per operar de veritat |
| `products` | buit = autodescoberta; o llistat separat per comes, ex. `HBAR-USDC,ADA-USDC` |
| `min_product_value` | `10` (ignora actius de menys valor) |
| `quote_currency_preference` | `USDC,EUR,USDT,USD` (separat per comes; als EU cal USDC/EUR) |
| `grid_levels` / `grid_spacing_pct` | `8` / `2.0` per defecte |
| `reserve_base_pct` | `25` (% de cada actiu que no es ven) |
| `cdp_api_key_name` | el `name` del JSON (o la teva API key) |
| `cdp_api_key_secret` | el `privateKey`/secret sencer |
| `telegram_enabled` | `true` |
| `telegram_bot_token` | token de @BotFather |
| `telegram_chat_id` | el teu `Id` |

## 5. Arrencar i comprovar

1. Engega l'add-on (etiqueta **Start**).
2. Al **Log** hi veuràs l'inici, el preu, les reixes i missatges `[DRY]` o ordres reals.
3. A **Settings → Devices** busquis els sensors `sensor.coinbase_trader_*`.

Operació manual mentre corre:
- `/pause` — no col·loca més ordres (manté les que hi ha).
- `/resume` — ho reprèn.
- `/status` — estat resumit.
- `/grid` — reixes i holdings.
- `/stop` — cancela totes les ordres obertes i atura el bot.

## 6. (Opcional) Fixar la graella

`grid_anchor_price` posa un preu d'ancoratge fix. Si és `0`, s'agafa el preu en el primer arrencada i es conserva entre reinicis (fitxer d'estat a la ruta de dades de l'add-on).

## Consell de seguretat
- Mai comparteixis `privateKey`.
- Si sospites compromís: regenera la key a CDP i desactiva la vella.

## Recepta per maximitzar el benefici

### La fórmula
Cada cicle complet (vendre dalt + recomprar baix) deixa aproximadament:

```
benefici per cicle ≈ (grid_spacing_pct − 0,50%) × notional
```

- `0,50%` = comissió d'anada i tornada maker (2 × `maker_fee_pct`).
- Amb `grid_spacing_pct: 2` → uns **1,5% net** per cicle.
- Per sota de ~`1,0%` d'espaiat el cicle deixa de ser rendible (el terra de comissions és 0,90%): el bot bloqueja els nivells no rendibles.
- **El bot no ven mai per sota del cost mitjà + marge** (`2 × maker_fee + min_profit_margin_pct`). Això evita vendre amb pèrdua i el "churn" de comprar i vendre al mateix preu. Si el preu cau per sota del teu cost, no ven: només compra, i espera que torni a pujar.
- El **cost base es calcula a cost mitjà** (no FIFO), de manera que el marge del guard i el P/L realitzat de cada venda coincideixen: una venda només deixa el marge net previst, mai una pèrdua "fantasma".
- Si el cost mitjà **puja** després de deixar ordres de venda obertes, el bot **cancel·la automàticament** les vendes que quedin per sota del break-even nou.

### Les palanques (per ordre d'impacte)
1. **Notional per nivell** (mida de cada ordre): com més gran, més € per cicle. Amb actius petits (10–30 USDC) cada ordre és petit. **Consolidar** en menys actius amb posicions grans multiplica el benefici més que tocar cap paràmetre.
2. **`grid_spacing_pct`**: més petit = més cicles però menys marge. Rang útil: **1,5–2,0**. Mínim absolut ~1,0%.
3. **Volatilitat**: la graella guanya quan el preu oscil·la dins d'un rang. En tendència forta, ven massa aviat (puja) o compra massa aviat (baixa).
4. **Comissions**: és la clau. Al tram "Intro" (0,25% maker) es perd mig cicle en fees. Pujar de tram (més volum) o operar menys i més gran millora el rendiment net.
5. **`reserve_base_pct`** (25%): la part que no es ven, per aprofitar pujades. Menys reserva = més vendes (més cicles); més reserva = menys, però captures tendències.
6. **`grid_levels` / `max_open_orders`**: més nivells = més oportunitats, però reparteixes el capital en ordres més petites (no baixis del mínim ~1 USDC per ordre).

### Pla per fases
- **Fase 1 (24h):** no tocar res. Observar. Confirmar que també col·loca **compres** sota el preu (amb ordres BUY visibles a `/grid`).
- **Fase 2 (si va bé):** `grid_spacing_pct: 1.5`, `grid_levels: 10`, `max_open_orders: 20`. Marge net ~1,0% i més cicles.
- **Fase 3:** consolidar posicions (canviar pols/actius petits per 2–3 amb posició gran).

### Revisió a 24h (checklist)
- `/status` → que les ordres obertes es moguin i els `cycles` pugin.
- `sensor.coinbase_trader_total_realized_pnl` i `sensor.coinbase_trader_recent_sells`.
- Que hi hagi **vendes i compres** (no només un costat).
- Cap `order rejected` al log.

### Regles d'or
- Mai `grid_spacing_pct` per sota de ~1,0%.
- Mantenir `use_signals: true` (evita comprar en caigudes fortes).
- Objectiu realista: **0,5–1,5% mensual** sobre el capital, segons tanto i volatilitat. No és una màquina d'imprimir.
- `/pause` abans de moviments grossos; `/stop` per aturar i cancel·lar tot.