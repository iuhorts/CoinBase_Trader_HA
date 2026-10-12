# Instal·lació a Home Assistant OS (RPi5)

## 0. Requisits
- Home Assistant OS de 64 bits (RPi5) amb connexió a internet.
- Compte Coinbase amb fons en EUR.
- (Opcional però recomanat) brokdat MQTT no cal — el bot publica sensors directament via l'API del supervisor.

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
| `product_id` | `ETH-EUR` (o canvia-ho) |
| `grid_levels` / `grid_spacing_pct` | `8` / `2.0` per defecte |
| `cdp_api_key_name` | el `name` del JSON (o la teva api key) |
| `cdp_api_key_secret` | el `privateKey` sencer (o el teu secret) |
| `telegram_enabled` | `true` |
| `telegram_bot_token` | el token de @BotFather |
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