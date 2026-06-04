"""Erzeugt eine realistische Beispiel-dashboard_data.json (gleiches Format wie
der echte Sammler). Nur für die erste Anzeige – wird im Betrieb überschrieben."""
import json
from datetime import datetime, date, timedelta

OPEN_H = 15
VEN = [
  ("Stadtpadel Kreuzberg","Kreuzberg",[("Double · Indoor",4,0.86,38),("Single · Indoor",2,0.72,30)],{"1h":0.28,"1,5h":0.48,"2h":0.24},[("Liga-Spieltag","alle Courts ganztägig",6)]),
  ("Padel Lounge Charlottenburg","Charlottenburg",[("Double · Indoor",6,0.79,40),("Double · Outdoor",2,0.55,30)],{"1h":0.22,"1,5h":0.50,"2h":0.28},[]),
  ("Base Padel Tempelhof","Tempelhof",[("Double · Indoor",5,0.81,36),("Single · Indoor",1,0.60,28)],{"1h":0.30,"1,5h":0.46,"2h":0.24},[]),
  ("Hexagon Padel Friedrichshain","Friedrichshain",[("Double · Indoor",4,0.83,39),("Double · Outdoor",3,0.50,28)],{"1h":0.26,"1,5h":0.49,"2h":0.25},[("Anfänger-Turnier","4 Courts vormittags",7)]),
  ("Padel Republic Mitte","Mitte",[("Double · Indoor",8,0.74,42)],{"1h":0.20,"1,5h":0.52,"2h":0.28},[]),
  ("Smash Padel Lichtenberg","Lichtenberg",[("Double · Indoor",3,0.70,32),("Double · Outdoor",2,0.46,24)],{"1h":0.34,"1,5h":0.44,"2h":0.22},[]),
  ("Padel Arena Spandau","Spandau",[("Double · Indoor",4,0.66,34),("Single · Outdoor",2,0.40,22)],{"1h":0.32,"1,5h":0.45,"2h":0.23},[]),
  ("Padelzeit Reinickendorf","Reinickendorf",[("Double · Indoor",3,0.69,33),("Double · Outdoor",2,0.42,24)],{"1h":0.30,"1,5h":0.47,"2h":0.23},[]),
  ("Volley Padel Neukölln","Neukölln",[("Double · Indoor",4,0.77,35)],{"1h":0.27,"1,5h":0.50,"2h":0.23},[]),
]
PER = {"today":(1,1.05,0.25),"week":(7,0.96,0.45),"month":(30,0.90,0.50)}

def clamp(x): return max(0.0, min(0.97, x))

venues=[]
for i,(name,dist,types,dur,events) in enumerate(VEN):
    metrics, by_type, by_duration = {}, {}, {}
    for pk,(days,boost,ms) in PER.items():
        cap=bk=rev=0.0; tlist=[]
        for (label,c,occ,p) in types:
            o=clamp(occ*boost); capH=c*OPEN_H*days; bkH=capH*o; r=bkH*p
            cap+=capH; bk+=bkH; rev+=r
            tlist.append({"label":label,"courts":c,"occupancy":round(o,4),"revenue":round(r,0)})
        metrics[pk]={"occupancy":round(bk/cap,4),"rev_measured":round(rev*ms,0),
                     "rev_estimated":round(rev*(1-ms),0),"booked_hours":round(bk,1),
                     "observed_days":days,"ccy":"EUR"}
        by_type[pk]=tlist
        by_duration[pk]={k:round(v,3) for k,v in dur.items()}
    ev=[]
    for (en,note,offset) in events:
        d=(date.today()+timedelta(days=offset)).isoformat()
        ev.append({"date":d,"name":en,"note":note})
    venues.append({"tenant_id":f"sample{i}","name":name,"district":dist,
        "courts":sum(c for _,c,_,_ in types),"metrics":metrics,
        "by_type":by_type,"by_duration":by_duration,"events":ev})

venues.sort(key=lambda v:v["metrics"]["month"]["rev_measured"]+v["metrics"]["month"]["rev_estimated"],reverse=True)
data={"updated_at":datetime.now().astimezone().isoformat(timespec="minutes"),
      "city":"Berlin","periods":["today","week","month"],"sample":True,"venues":venues}
with open("dashboard_data.json","w",encoding="utf-8") as f:
    json.dump(data,f,ensure_ascii=False,indent=2)
print("geschrieben: docs/data/dashboard_data.json  |  Clubs:",len(venues))
