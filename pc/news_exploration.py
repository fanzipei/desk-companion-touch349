# Copyright 2026 fanzipei. SPDX-License-Identifier: Apache-2.0
"""Title-only novelty allocation, diversity, and bounded feedback statistics."""
from collections import Counter, defaultdict, deque
import copy
import json
import math
import re
import time

from .news_feeds import atomic_json
from .core import delivered_news, article_digest

TOPICS = {
    "space":("航天与天文",r"nasa|\bspace\b|astronom|rocket|satellite|宇宙|航天|太空|卫星|天文"),
    "energy":("能源与产业转型",r"energy|battery|nuclear|solar|electric vehicle|能源|电池|核能|光伏|电动车"),
    "climate":("气候与环境",r"climate|carbon|environment|ocean|biodiversity|气候|碳排|环境|海洋|生态"),
    "health":("生命科学与健康科技",r"medical|health|cancer|genetic|brain|biotech|医学|医疗|癌|基因|大脑|生物"),
    "science":("基础科学与发现",r"science|scientist|quantum|physics|chemistry|科学|量子|物理|化学|研究发现"),
    "geopolitics":("国际关系与地区政治",r"war|ceasefire|election|diploma|military|europe|africa|战争|停火|选举|外交|军事|欧洲|非洲"),
    "macro":("宏观经济与公共政策",r"inflation|interest rate|central bank|trade|econom|fed\b|ecb\b|通胀|利率|央行|贸易|经济|美联储"),
    "markets":("投资与市场结构",r"stock|ipo|bond|fund|invest|crypto|bank|finance|股票|上市|债券|基金|投资|加密|银行|金融"),
    "ai":("人工智能与机器人",r"\bai\b|artificial intelligence|llm|robot|anthropic|openai|qwen|人工智能|大模型|机器人|具身"),
    "computing":("计算、芯片与软件",r"chip|processor|software|computer|security|apple|芯片|处理器|软件|计算|安全|苹果"),
    "industry":("产业与创新",r"manufactur|startup|industry|business|transport|制造|创业|产业|企业|交通"),
}
STOP = {"the","and","for","with","from","that","this","says","will","after","into","over","new","its","are","has"}


def topic_of(item):
    text=item.get("title","").lower()
    for topic,(_,pattern) in TOPICS.items():
        if re.search(pattern,text):return topic
    if item.get("feed")=="nasa":return "space"
    if item.get("feed") in ("mit","sciencedaily"):return "science"
    return {"politics":"geopolitics","investment":"macro","technology":"computing"}.get(item.get("category"),"general")


def tokens(title):
    words=set(re.findall(r"[a-z0-9]{2,}",title.lower()))-STOP
    chinese=''.join(re.findall(r"[\u3400-\u9fff]",title))
    return words|{chinese[i:i+2] for i in range(len(chinese)-1)}


def same_event(a,b):
    if a.get("url")==b.get("url"):return True
    left,right=tokens(a["title"]),tokens(b["title"])
    shared=len(left&right)
    return shared>=5 and shared/max(1,min(len(left),len(right)))>=.82


class ExplorationPlanner:
    def __init__(self,directory):
        self.path=directory/"news-exploration.json"
        self.state=json.loads(self.path.read_text(encoding="utf8")) if self.path.exists() else {
            "version":1,"cycle":0,"topics":{},"articles":{},"feedback_seen":[],"recent_topics":[]}

    def plan(self,candidates,config,snapshot,feedback,now=None):
        now=time.time() if now is None else now
        state=copy.deepcopy(self.state)
        stats=state["topics"];seen=set(state["feedback_seen"])
        if not state['articles'] and state['cycle']==0:
            # The first upgraded round must not mistake old favourite topics for
            # unseen interests. Only delivered briefs/current items seed exposure;
            # title-only candidate cache entries do not count as impressions.
            current={i['id'] for i in snapshot.get('news',[])}
            for item in snapshot.get('news_history',[])+snapshot.get('news',[]):
                key=str(item['id'])
                if key in state['articles'] or (item['id'] not in current and not (item.get('summary_basis')=='article' and item.get('summary'))):continue
                topic=item.get('recommendation_topic') or topic_of(item)
                row=stats.setdefault(topic,{'shown':0,'pinned':0,'opened':0,'deleted':0,'browsed':0});row['shown']+=1
                state['articles'][key]={'topic':topic,'lane':item.get('recommendation_lane','interest'),'at':int(now)}
                state['recent_topics'].append(topic)
        for event in feedback:
            key=f"{event['id']}:{event['kind']}"
            if key in seen:continue
            seen.add(key)
            context=state["articles"].get(str(event["id"]),{})
            topic=context.get("topic") or event.get("topic") or topic_of(event)
            row=stats.setdefault(topic,{"shown":0,"pinned":0,"opened":0,"deleted":0,"browsed":0})
            if event["kind"] in ("pinned","opened","deleted","browsed"):row[event["kind"]]+=1
        state["feedback_seen"]=list(dict.fromkeys(state["feedback_seen"]+[f"{e['id']}:{e['kind']}" for e in feedback]))[-4096:]
        delivered_ids, delivered_urls = delivered_news(snapshot)
        delivered=set(state["articles"]) | {str(identifier) for identifier in delivered_ids}
        clean=[]
        for item in sorted(candidates,key=lambda i:i.get("time",0),reverse=True):
            if str(item['id']) in delivered or article_digest(item['url']) in delivered_urls:continue
            if not now-48*3600<=item.get("time",0)<=now+600:continue
            if re.search(r"buy one get one|coupon code|weekend.*sale|领券|优惠券|限时抢购",item["title"],re.I):continue
            if any(same_event(item,previous) for previous in clean):continue
            clean.append({**item,"recommendation_topic":topic_of(item)})
        cycle=state["cycle"]
        groups=defaultdict(deque)
        for item in sorted(clean,key=lambda i:(str(i['id']) in delivered,-i.get('time',0))):groups[item.get('publisher',item.get('domain',''))].append(item)
        names=sorted(groups);names=names[cycle%len(names):]+names[:cycle%len(names)] if names else []
        pool=[];maximum=max(128,config['news_limit']*3)
        while names and len(pool)<maximum:
            for name in list(names):
                if groups[name]:pool.append(groups[name].popleft())
                if not groups[name]:names.remove(name)
                if len(pool)>=maximum:break
        budget=min(len(pool),max(1,math.ceil(config['news_limit']*config.get('news_exploration_percent',30)/100))) if pool else 0
        fresh=[i for i in pool if str(i['id']) not in delivered]
        reserved=[];topic_counts=Counter();sources=Counter();recent=Counter(state['recent_topics'][-24:])
        def score(item):
            topic=item['recommendation_topic'];row=stats.get(topic,{})
            shown=row.get('shown',0);positive=8*row.get('pinned',0)+4*row.get('opened',0)+min(row.get('browsed',0),8)
            # Smoothed, capped feedback adjusts exploration, never bans a whole topic.
            affinity=max(-.5,min(.6,(positive-4*row.get('deleted',0))/(10+positive+4*row.get('deleted',0))))
            return 3/math.sqrt(1+shown)+math.sqrt(math.log(2+cycle)/(1+shown))+affinity-.15*recent[topic] \
                -2*topic_counts[topic]-.6*sources[item.get('publisher','')]+.3*max(0,1-(now-item.get('time',0))/(48*3600))
        while fresh and len(reserved)<budget:
            chosen=max(fresh,key=lambda i:(score(i),i.get('time',0),-i['id']))
            reserved.append(chosen['id']);fresh.remove(chosen)
            topic_counts[chosen['recommendation_topic']]+=1;sources[chosen.get('publisher','')]+=1
        return {'state':state,'pool':pool,'reserved':reserved,'budget':len(reserved),'delivered':delivered}

    @staticmethod
    def mix(stories,reserved,limit):
        unique={item['id']:item for item in stories}
        exploration=[unique[i] for i in reserved if i in unique]
        interest=[i for i in unique.values() if i['id'] not in reserved]
        result=[];topics=Counter();sources=Counter()
        # Reserve exploration slots before applying interest diversity limits.
        for item in exploration:
            result.append({**item,'recommendation_lane':'explore'});topics[item['recommendation_topic']]+=1;sources[item.get('publisher','')]+=1
        deferred=[]
        for item in interest:
            if topics[item['recommendation_topic']]>=max(2,math.ceil(limit/3)) or sources[item.get('publisher','')]>=max(2,math.ceil(limit/4)):
                deferred.append(item);continue
            if len(result)<limit:result.append({**item,'recommendation_lane':'interest'});topics[item['recommendation_topic']]+=1;sources[item.get('publisher','')]+=1
        for item in deferred:
            if len(result)<limit:result.append({**item,'recommendation_lane':'interest'})
        explore=deque(i for i in result if i['recommendation_lane']=='explore');known=deque(i for i in result if i['recommendation_lane']=='interest')
        mixed=[]
        while known or explore:
            for _ in range(2):
                if known:mixed.append(known.popleft())
            if explore:mixed.append(explore.popleft())
        return mixed[:limit]

    def remember(self,plan,stories,now=None):
        state=plan['state'];now=int(time.time() if now is None else now)
        for item in stories:
            key=str(item['id']);topic=item['recommendation_topic']
            if key not in state['articles']:
                row=state['topics'].setdefault(topic,{'shown':0,'pinned':0,'opened':0,'deleted':0,'browsed':0});row['shown']+=1
                state['recent_topics'].append(topic)
            state['articles'][key]={'topic':topic,'lane':item['recommendation_lane'],'at':now}
        state['articles']=dict(list(state['articles'].items())[-2048:]);state['recent_topics']=state['recent_topics'][-128:]
        state['cycle']+=1
        atomic_json(self.path,state);self.state=state
