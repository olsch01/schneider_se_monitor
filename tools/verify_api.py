"""Verify the saved tokens work: REST, Cognito refresh, websocket. Prints no secrets."""
import asyncio, json, pathlib, time
import aiohttp

ROOT = pathlib.Path(__file__).resolve().parent.parent
TOK = ROOT / ".tokens.json"
API = "https://app-api-us.clipsalcortex.com/v1/"
KEY = "PudhZveaHq9gTjxNU0XjJ7Tiy520eqQI20bKi683"

async def main():
    t = json.loads(TOK.read_text())
    async with aiohttp.ClientSession() as s:
        # 1. Cognito refresh
        r = await s.post("https://cognito-idp.us-east-1.amazonaws.com/", headers={
            "Content-Type": "application/x-amz-json-1.1",
            "X-Amz-Target": "AWSCognitoIdentityProviderService.InitiateAuth"},
            data=json.dumps({"ClientId": "3o2v8tokrthvv2uk0mj3u0sgva", "AuthFlow": "REFRESH_TOKEN_AUTH",
                             "AuthParameters": {"REFRESH_TOKEN": t["refresh_token"]}}))
        body = await r.json(content_type=None)
        print("refresh:", r.status, sorted(body.get("AuthenticationResult", body)))
        if r.status == 200:
            t["id_token"] = body["AuthenticationResult"]["IdToken"]
            t["access_token"] = body["AuthenticationResult"]["AccessToken"]
            TOK.write_text(json.dumps(t))
        for label, tokname, extra in [("id_token", "id_token", {}), ("id_token+key", "id_token", {"x-api-key": KEY}),
                                      ("access_token", "access_token", {})]:
            r = await s.get(API + "sites", headers={"Authorization": f"Bearer {t[tokname]}", **extra})
            print(f"sites with {label}:", r.status)
        h = {"Authorization": f"Bearer {t['id_token']}"}
        sites = (await (await s.get(API + "sites", headers=h)).json())["data"]
        sid = sites[0]["site_id"]; tz = sites[0]["timezone"]
        print("site:", sid, tz)
        for ep, q in [("energy_usage", {"start_date": time.strftime("%Y-%m-%d"), "end_date": time.strftime("%Y-%m-%d"), "groupby": "day"})]:
            r = await s.get(f"{API}sites/{sid}/{ep}", headers=h, params=q)
            d = await r.json(); print(ep, r.status, {k: v for k, v in d[0].items() if k != "appliances"})
        async with s.ws_connect("wss://app-ws-us.clipsalcortex.com/", params={"token": t["id_token"]}) as ws:
            await ws.send_json({"action": "subscribeSiteLiveData", "clipsal_solar_id": sid})
            n = 0
            async for m in ws:
                d = json.loads(m.data)
                if d.get("type") == "realtime_update":
                    p = d["payload"]; n += 1
                    print("realtime W:", round(p["consumption"]), "| devices on:", [(x["name"], round(x["w"])) for x in p["devices"] if x["w"]])
                    if n == 3: break
                else:
                    print("ws:", d.get("type"), d.get("payload", {}).get("message"))

asyncio.run(main())
