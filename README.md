# 功能介绍
  * spider.py、spider.json为检测脚本及相应设置文件。支持youtube频道 直播留言 社区帖子 推送、twitter用户信息 用户推特 推特搜索、twitcast频道 直播留言、fanbox用户信息 帖子的检测、bilibili频道 直播留言、lol steam osu用户数据，支持推送到qq用户、qq群、喵提醒、discord、telegram。  
  * release中发布的exe版本可以在windows中直接运行，无需依赖、点开即用。

感谢[太古oo](https://www.bilibili.com/read/cv4603796)提供的灵感和检测方法，感谢[24h-raspberry-live-on-bilibili](https://github.com/chenxuuu/24h-raspberry-live-on-bilibili/tree/master)与[blivedm](https://github.com/xfgryujk/blivedm)的b站弹幕接口。  


# 环境依赖
## 检测脚本本体
##### 安装方法
在命令行中运行`pip3 install requests; pip3 install bs4; pip3 install lxml; pip3 install websocket-client`安装脚本依赖的python库，将spider.py和spider.json文件下载到相同的目录（注意至少还需要在配置文件中设置cookies和要推送的qq账户才能正常运行）。
##### 启动方法
在命令行中运行`python3 spider.py`，按照提示选择配置文件。

## 推送方式
##### qq推送
基于[go-cqhttp](https://github.com/Mrs4s/go-cqhttp)  
在命令行中运行`wget "https://github.com/Mrs4s/go-cqhttp/releases/download/v1.0.0-beta1/go-cqhttp_linux_386.tar.gz" ; tar -xvf go-cqhttp_linux_386.tar.gz ; chmod +x go-cqhttp ; ./go-cqhttp`运行mirai机器人并按照提示登录。初次运行后关闭mirai机器人，修改config.json中的内容为config.json设置示例中的内容，注意修改其中'用作机器人的QQ号'为机器人的qq号。之后再次运行`./go-cqhttp`即可。

##### 喵提醒
在喵提醒微信公众号中选择'提醒'-'添加提醒'，完成设置后将会收到一个喵提醒号。更加详细的说明可以在其微信公众号中查看。

##### discord推送
在discord频道右键-编辑频道-webhook-创建webhook(需要编辑webhook权限)，完成设置后将会产生一个webhook链接。

##### telegram推送
在telegram中搜索botfather-发送/newbot-输入bot用户名-输入bot id，完成设置后将会产生一个bot token。用户群聊或频道号为邀请链接中的t.me/后面的部分，如果需要在群聊或频道中发言需要先邀请bot进群。


# 脚本详解
### 脚本运行原理
  * 脚本将会按照设置文件中的设置来启动许多子监视器线程以完成不同的监视任务，你可以为每一个监视器线程指定不同的运行参数，也可以让一些监视器线程共用一些运行参数的同时为每个监视器添加各自特有的运行参数。
  * 每个子监视器线程都会定时检测指定的内容，如youtube视频列表、twitter用户信息、twitcast直播状态等，并将更新的信息与指定的关键词进行匹配，对符合条件的信息向用户进行推送。
  * 关键词匹配基本由设置文件中的"vip_dic"和"word_dic"指定，"vip_dic"中的关键词用于匹配检测的频道、发送的用户、提及的用户之类的信息，"word_dic"中的关键词用于匹配标题、简介、消息内容之类的信息；关键词有对应的"权重类型"和相应的"权重值"。当匹配到相应的关键词时，关键词对应的"权重类型"的"权重值"将会被分别记录并累加。例如当检测的用户发送了一条消息`小红和小蓝在玩`时，如果`"word_dic": {'小红': {'red': 1, 'small': 1}, '小蓝': {'blue': 1, 'small': 2}}`，则这条消息的"权重"将会为`{'red': 1, 'blue': 1, 'small': 3}`。
  * 用户推送由设置文件中的"push_dic"指定，其中除了如推送类型和qq号等基本信息之外，还有指定"接收权重"的参数"color_dic"，当一条消息的"推送权重"中有任何一个"权重类型"的"权重值"大于或等于"接收权重"中的指定值时这条消息就会向该用户推送。对于上述例子中的消息，如果用户的`"color_dic": {'green': 1, 'small': 2}`，那么因为"small"这种色彩的值大于用户接收权重的值，所以这条消息将会向该用户推送。


### 配置文件详解
```
{
  "submonitor_dic": {  # 子监视器列表
    "YoutubeLive 神楽めあ": {"class": "YoutubeLive", "target": "UCWCc8tO-uUl_7SJXIKJACMw", "target_name": "神楽めあ", "config_name": "youtube_config"},
    "TwitterUser 神楽めあ": {"class": "TwitterUser", "target": "KaguraMea_VoV", "target_name": "神楽めあ", "config_name": "twitter_config"}
    # "子监视器名称 用于特异的标记子监视器 不能重复": {"class": "子监视器类名 用于启动不同类型的子监视器来完成不同的监视任务 与脚本中的类名相同", "target": "要检测的频道编号 注意大小写敏感", "target_name": "检测的频道名称 将会被添加到推送消息中表明消息来源", "config_name": "要使用的配置名称"}
  },
  
  "youtube_config": {  # 配置名称 即上面"config_name"后指定的名称
    "interval": 180, # 检测循环间隔
    "timezone": 8, # 推送时转换为指定的时区 如北京时间即东八区则为8
    "vip_dic": {  # 用于匹配"target"中指定的频道或者直播留言的发送者 注意大小写敏感
      "UCWCc8tO-uUl_7SJXIKJACMw": {"mea": 10},
      "UCu5eCcfs67GkeCFbhsMrEjA": {"mea": 10},
      "UCZU5rKvh3aAFs1PyyfeLWcg": {"mea": 10}
    },
    "word_dic": {  # 用于匹配直播标题和简介或者直播留言的的内容
      "神楽めあ": {"mea": 4},
      "めあちゃん": {"mea": 4},
      "めあだ": {"mea": 4},
      "神楽": {"mea": 2},
      "めあ": {"mea": 2},
      "かぐら": {"mea": 2},
      "メア": {"mea": 2}
    },
    "cookies": {}, # 检测所用的cookies，可以在浏览器中打开youtube页面时按下f12，在"网络"中寻找POST类型的请求并复制其cookies即可，注意可能需要删除开头的"{请求cookies"和结尾的多余的"}"，不指定可以留空或删除此项
    "proxy": {"http": "socks5://127.0.0.1:1080","https": "socks5://127.0.0.1:1080"}, # 指定代理，如果使用非sock5代理可设置为{"http": "127.0.0.1:1080", "https": "127.0.0.1:1080"}，不使用代理可以留空或删除此项
    "push_list": [ # 指定推送对象
        {"type": "qq_user", "id": "qq号", "port": 5700, "color_dic": {"mea": 1}}, # qq_user与qq_group可以通过"ip"键来指定推送到的ip地址，默认为127.0.0.1。修改此设置需要同时修改qq机器人的监听ip，如果指定为本地以外的ip地址可能会导致安全性问题。
        {"type": "qq_group", "id": "qq群号", "port": 5700, "color_dic": {"mea": 1}},
        {"type": "miaotixing", "id": "喵提醒号", "color_dic": {"mea": 1}},
        {"type": "miaotixing_simple", "id": "喵提醒号", "color_dic": {"mea": 1}}, #不推送文字，防止语音或者短信推送失效
	{"type": "discord", "id": "discord webhook链接", "color_dic": {"mea": 1}, "proxy": {"http": "socks5://127.0.0.1:1080","https": "socks5://127.0.0.1:1080"}}, #推送对象也可以指定代理
	{"type": "telegram", "id": "telegram @用户群聊频道名 或 聊天id号(群组聊天id号前有"-")", "bot_id": "telegram bot token", "color_dic": {"mea": 1}}
    ]
 }
}
```

  * 监视器运行所需的参数由"submonitor_dic"中的项和其中"config_name"指向的配置组成，其中"class"、"target"、"target_name"、"config_name"四个项目作为必选参数需要在"submonitor_dic"中指定，其他参数既可以添加在"submonitor_dic"中也可以添加在"config_name"指向的配置中；注意当有同名参数同时存在于两个位置时，"submonitor_dic"中的参数将会生效。例如spider.json中就在部分"submonitor_dic"监视器信息中额外指定了"interval"的值，以便让这些监视有更短的检测间隔。  
  * 基于Monitor类的监视器可以启动自己的子监视器，只要指定的配置中还有"submonitor_dic"项并且添加了相应的子监视器信息的话。例如spider.json中就先启动了基于Monitor类的"Youtube"、"Twitter"、"Fanbox"等几个监视器，这几个监视器又启动了各自配置中"submonitor_dic"项中指定的子监视器。
  * YoutubeLive、TwitcastLive和BilibiliLive监视器可以在一定情况下启动自己的YoutubeChat、TwitcastChat和BilibiliChat子监视器，这些子监视器将会继承父监视器的config_name所指向的配置，但不会继承父监视器submonitor_dic中额外指定的参数（即submonitor_dic中指定的参数不被继承，而config_name中指定的参数将被继承），如果想让Live监视器和Chat监视器有不同的设置则可以分别在submonitor_dic和config_name所指向的配置中设定两者的参数。
  * YoutubeChat、TwitcastChat和BilibiliChat子监视器会对直播评论发送者和评论内容进行关键词匹配（用于监视本人出现在其他人的直播间或者其他直播提到特定内容的情况）。为了防止vip本人的直播中的评论触发推送，如果子监视器的"target"项和"vip_dic"中关键词匹配的话，"推送权重"将会减去"vip_dic"中相应"关键词的权重"。另外为了防止某场直播中的出现大量评论频繁触发推送，每次推送时如果"推送权重"中如果有大于0的项，那么后续推送中这种权重类型将会被增加1的推送惩罚；当权重类型名字中含有"vip"字样时，这种权重类型不会受到推送惩罚。
  
### 子监视器详解
__子监视器类名__|作用|__通用必选参数__|vip_dic匹配内容|word_dic匹配内容|cookies作用|__特有可选参数__|说明
:---|:---|:---|:---|:---|:---|:---|:---
Monitor|作为基本监视器管理子监视器组|interval|||||
YoutubeLive|监视youtube直播和视频|interval、timezone、vip_dic、word_dic、cookies、proxy、push_list|target|标题、简介|可留空|"standby_chat"，"standby_chat_onstart"，"no_chat"，"status_push"，"regen"，"regen_amount"|standby_chat为是否检测待机直播间的弹幕 默认为"False" 可选"True"，standby_chat_onstart是否检测在第一次检测时已开启的待机直播间的弹幕 默认为"False" 可选"True"，no_chat为是否不记录弹幕 默认为"False" 可选"True"，status_push为推送相应类型的更新 默认为"等待\|开始\|结束\|上传\|删除"，regen为推送惩罚恢复间隔 默认为"False" 可选"间隔秒数"，regen_amount为每次推送惩罚恢复量 默认为"1" 可选"恢复数量"
YoutubeChat|监视youtube直播评论|同上|父监视器target（取负）、直播评论发送频道|直播评论文字|||通常由YoutubeLive监视器创建 无需在配置文件中指定
YoutubeCom|监视youtube社区帖子|同上|target|帖子文字|付费帖子，可留空|||
YoutubeNote|监视cookies对应用户的通知|同上||通知文字内容（包括superchat）|用户通知，必要|||
TwitterUser|监视twitter用户基本信息|同上|target||必要|"no_increase"，"no_repeat"|no_increase为是否不推送推文和媒体数量的增加 默认为"False" 可选"True"，no_repeat为是否不推送短时间内重复的推文和媒体数量 默认为"False" 可选"间隔秒数"
TwitterTweet|监视twitter用户的推文|同上|target、推文@对象|推文文字（包括#、@和链接）|必要|||
TwitterSearch|监视推特搜索结果|同上|target、推文@对象|推文文字（包括#、@和链接）|必要|"only_live", "only_liveorvideo"|only_live为是否只推送有链接指向正在进行的youtube直播的推文 默认为"False" 可选"True"，only_liveorvideo为是否只推送有链接指向youtube直播或视频的推文 默认为"False" 可选"True"，当两者同时开启时则only_liveorvideo生效
TwitcastLive|监视twitcast直播|同上|target|标题|可留空|"no_chat"，"status_push"，"regen"，"regen_amount"|no_chat为是否不记录弹幕 默认为"False" 可选"True"，status_push为推送相应类型的更新 默认为"开始\|结束"，regen为推送惩罚恢复间隔 默认为"False" 可选"间隔秒数"，regen_amount为每次推送惩罚恢复量 默认为"1" 可选"恢复数量"
TwitcastChat|监视twitcast直播评论|同上|父监视器target（取负）、直播评论发送频道|直播评论文字|||通常由TwitcastLive监视器创建 无需在配置文件中指定
FanboxUser|监视fanbox用户基本信息|同上|target||可留空|||
FanboxPost|监视fanbox用户帖子|同上|target|帖子文字|付费帖子，可留空|||
BilibiliLive|监视bilibili直播|同上|target|标题|可留空|"offline_chat"，"simple_mode"，"no_chat"，"status_push"，"regen"，"regen_amount"|offline_chat为是否监测离线直播间的弹幕 默认为"False" 可选"True"，simple_mode为只推送弹幕文字 如果为数字则会将相应数量的弹幕整合推送 默认为"False" 可选"合并数量"，no_chat为是否不记录弹幕 默认为"False" 可选"True"，status_push为推送相应类型的更新 默认为"开始\|结束"，regen为推送惩罚恢复间隔 默认为"False" 可选"间隔秒数"，regen_amount为每次推送惩罚恢复量 默认为"1" 可选"恢复数量"
BilibiliChat|监视bilibili直播评论|同上|父监视器target（取负）、直播评论发送频道|直播评论文字|||通常由BilibiliLive监视器创建 无需在配置文件中指定，只能使用http代理 格式应为"proxy": {"http": "ip地址:端口号"}
LolUser|监视lol比赛状况与最近比赛结果|同上|target||可留空|"user_region"，"ingame_onstart"|user_region为账号所在的地区 即[jp.op.gg](https://jp.op.gg/summoner/l=en_US&userName=%E3%81%8B%E3%81%90%E3%82%89%E3%82%81%E3%81%82%E3%81%A3)网站开头部分 默认为"jp"，ingame_onstart为是否在初次启动时就在游戏中的情况下进行推送 默认为"True" 可选"False"，由于op.gg最短更新间隔限制为120秒 所以将检测间隔设置为小于120秒意义不大
SteamUser|监视steam在线状况与基本信息|同上|target||查看自己或好友可见的内容，可留空|"online_onstart"|online_onstart为是否在初次启动时就在线的情况下进行推送 默认为"True" 可选"False"
OsuUser|监视osu在线状况与基本信息|同上|target||查看自己或好友可见的内容，可留空|"online_onstart"|online_onstart为是否在初次启动时就在线的情况下进行推送 默认为"True" 可选"False"

### 常见故障
##### 运行闪退
请确保使用的配置文件（默认为spider.json）为标准的json格式，py脚本与json配置文件为utf-8编码。
##### 运行后出现很多error信息
如果同时存在youtube和twitter监视器的error信息，可能是网络原因导致的，由于在脚本刚开始运行时会产生比较多的请求，可能会导致一些请求超时。可以尝试等待一段时间或者重启脚本。  
如果只有twitter监视器的error信息，可能是twitter配置下的cookies不正确导致的，请确保cookies也为json格式，下面是一个cookies示例。
```
"cookies"： {"_ga":"12345678","_gid":"12345678","_twitter_sess":"12345678","ads_prefs":""12345678"","auth_token":"12345678","csrf_same_site":"12345678","csrf_same_site_set":"12345678","ct0":"12345678","dnt":"12345678","gt":"12345678","guest_id":"12345678","kdt":"12345678","lang":"12345678","personalization_id":""12345678"","remember_checked_on":"12345678","rweb_optin":"12345678","twid":"u=12345678"},
```
##### 更新后无法推送
检查配置文件中的push_list项是否已调整为新的格式

# 想做的事
  * 添加bilibili视频与动态监视器
  * 更换youtube视频信息接口
  * 更换twitcast评论接口
  * 对视频标题与简介中出现的关键字也减去相应权重
  * 添加apex、amazon等监视器
