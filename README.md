# 功能介绍
  * spider.py、spider.json和pause.json为检测脚本及相应设置文件。支持youtube频道 直播留言 社区帖子 推送、twitter用户信息 用户推特 推特搜索、twitcast频道 直播留言、fanbox用户信息 帖子的检测和推送。  
  
  * pausebot.py和plugins为启动nonebot机器人的脚本及插件。用于让相关用户可以通过qq对pause.json中的设置进行查看和修改。  

感谢[太古oo](https://www.bilibili.com/read/cv4603796)提供的灵感和检测方法。  


# 原理说明
主要由三部分构成，coolq机器人和coolq-http-api插件、nonebot机器人和相应插件、检测脚本。  
coolq机器人和coolq-http-api插件作为qq客户端用于直接和用户收发信息，其中coolq-http-api插件一方面通过接受检测脚本发送到的指定端口的http请求（默认为5700端口）向qq用户推送消息，另一方面通过websocket和nonebot机器人建立连接、接受并处理qq用户发来的消息，让用户可以通过qq查看和修改推送设置。  


# 环境依赖和安装方法
  * coolq机器人（[windows免费版](https://cqp.cc/)、[windows收费版](https://cqp.cc/t/14901)、[linux docker版](https://cqp.cc/t/34558)）和[coolq-http-api插件](https://github.com/richardchien/coolq-http-api/releases)  
  coolq机器人在windows中直接下载运行即可，在linux中需要在docker中安装`docker pull coolq/wine-coolq`、建立设置文件夹`mkdir 'coolq机器人设置文件存放路径'`、运行`docker run --name=coolq --rm -p 5700:5700 -p 9000:9000 -v 刚刚建立的设置文件存放路径:/home/user/coolq -e VNC_PASSWD=网页登陆密码 -e COOLQ_ACCOUNT=你的qq账号 -e CQHTTP_SERVE_DATA_FILES=yes coolq/wine-coolq`、在浏览器中打开`http://你的服务器IP:9000`并输入刚刚设置的网页登陆密码登录。  
  在运行一次后coolq机器人的设置文件夹中将会产生几个文件夹，需要将io.github.richardchien.coolqhttpapi.cpk文件(coolq-http-api插件本体)放到app文件夹下，重启coolq机器人后在设置中启用coolq-http-api插件，再次重启coolq机器人后设置文件夹的app\io.github.richardchien.coolqhttpapi\config或data\app\io.github.richardchien.coolqhttpapi\config中将会产生一个json文件，将其中的内容替换为此项目中相应设置示例的内容即可。

  * [nonebot机器人](https://nonebot.cqp.moe/)、启动脚本(pausebot.py)和相应插件(plugins文件夹)  
  在命令行中运行`pip3 install nonebot`即可安装nonebot机器人本体，再将pausebot.py和plugins文件夹下载到本地任意目录即可。

  * 检测脚本和相应设置文件  
  在命令行中运行`pip3 install requests; pip3 install bs4; pip3 install lxml`安装脚本依赖的python库，将spider.py、spider.json和pause.json文件下载到和pausebot.py和plugins文件夹的相同的目录即可（注意至少还需要在spider.json中设置cookies和要推送的qq账户才能正常运行）。  


# 启动和使用方法
## coolq机器人
#### 启动coolq机器人
在安装设置完成后运行并登录即可。  


## nonebot机器人
#### 启动nonebot机器人
在命令行中运行`python3 pausebot.py`，按照提示设置ip地址和端口。此时如果配置正确的话命令行应该会显示形如`[2019-01-26 16:23:17,159] 172.29.84.18:50639 GET /ws/api/ 1.1 101 - 986``[2019-01-26 16:23:17,201] 172.29.84.18:53839 GET /ws/event/ 1.1 101 - 551`的两条提示。  

#### 通过nonebot机器人查看和修改设置
在qq中向coolq机器人登录的qq账号发送消息（私聊消息需要为好友，在同一个群聊中则没有限制），发送`\check`可以检测推送阻力，发送`\pause 数字`可以设置推送阻力，另外`\echo 文字`或`复读文字`可以让bot复读，`\test`或`在吗`可以让bot回复。  


## 检测脚本
#### 启动检测脚本
在命令行中运行`python3 spider.py`，按照提示选择配置文件。  

### 脚本运行原理
  * 脚本将会按照设置文件中的设置来启动许多子监视器线程以完成不同的监视任务，你可以为每一个监视器线程指定不同的运行参数，也可以让一些监视器线程共用一些运行参数，同时为每个监视器添加各自特有的运行参数。脚本内置了一些预设的子监视器，你也可以通过继承脚本中的Monitor类来编写自己的子监视器。脚本目前只有推送到qq的功能，不过你也可以在pushall方法中添加自己的内容来增加新的推送功能。
  * 每个子监视器线程都会定时检测指定的内容，如youtube视频列表、twitter用户信息、twitcast直播状态等，并将更新的信息与指定的关键词进行匹配，对符合条件的信息向用户进行推送。
  * 关键词匹配基本由设置文件中的"vip_dic"和"word_dic"指定，"vip_dic"中的关键词用于匹配检测的频道、发送的用户、提及的用户之类的信息，"word_dic"中的关键词用于匹配标题、简介、消息内容之类的信息；当匹配到相应的关键词时，关键词对应的"推送色彩"将会被记录并累加。例如当检测的用户发送了一条消息`小红和小蓝在玩`时，如果`"word_dic": {'小红': {'red': 1, 'small': 1}, '小蓝': {'blue': 1, 'small': 2}}`，则这条消息的"推送色彩"将会为`{'red': 1, 'blue': 1, 'small': 3}`。
  * 用户推送由设置文件中的"push_dic"指定，其中除了诸如用户类型和qq号等基本信息之外还有指定"接收色彩"的参数"color_dic"，当一条消息的"推送色彩"中有任何一个对应类型的值大于或等于"接收色彩"中的指定值时这条消息就会向该用户推送。对于上述例子中的消息，如果用户的`"color_dic": {'green': 1, 'small': 2}`，那么因为"small"这种色彩的值大于用户接受色彩的值，所以这条消息将会向这该用户推送。
  * 当qq用户或qq群内向机器人账号发送`\pause 数字`指令后，相应qq账号或qq群的"推送阻力"将会被记录到pause.json文件中。在推送时相应账号的所有"接收色彩"将会增加"推送阻力"的值，也就是对推送内容更加"挑剔"，以减少不相关内容的骚扰。
  * "推送色彩"、"接收色彩"、"推送阻力"都可以设定为负值，或许可以产生更灵活的用法。


### 配置文件详解
```
{
  "interval": 180,  # 主监视器循环间隔 以秒为单位
  
  "submonitor_dic": {  # 子监视器列表
    "YoutubeLive 神楽めあ": {"class": "YoutubeLive", "target": "UCWCc8tO-uUl_7SJXIKJACMw", "target_name": "神楽めあ", "config_name": "youtube_config"},
    "TwitterUser 神楽めあ": {"class": "TwitterUser", "target": "KaguraMea_VoV", "target_name": "神楽めあ", "config_name": "twitter_config"}
    # "子监视器名称 用于特异的标记子监视器 不能重复": {"class": "子监视器类名 用于启动不同类型的子监视器来完成不同的监视任务 与脚本中的类名相同", "target": "要检测的频道编号 注意大小写敏感", "target_name": "检测的频道名称 将会被添加到推送消息中表明消息来源", "config_name": "要使用的配置名称"}
  },
  
  "youtube_config": {  # 配置名称 即上面"config_name"后指定的名称
    "interval": 180,
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
    "push_dic": {  # 指定推送对象
      "pushlist_qq": [
        {"type": "user", "id": "1234567", "port": 5700, "color_dic": {"mea": 1}},
        {"type": "group", "id": "7654321", "port": 5700, "color_dic": {"mea": 1}}
			]
		}
	}
}
```

  * 每一个子监视器还可以继续启动自己的子监视器，只要指定的配置中还有"submonitor_dic"项并且添加了相应的子监视器信息的话。例如spider.json中就先启动了基于Monitor类的"Youtube"、"Twitter"、"Fanbox"三个监视器，这三个监视器又启动了各自配置中"submonitor_dic"项中指定的子监视器。
  * 子监视器运行所需的参数由"submonitor_dic"中的项和其中"config_name"指向的配置组成，其中"class"、"target"、"target_name"、"config_name"四个项目作为必选参数需要在"submonitor_dic"中指定，其他参数既可以添加在"submonitor_dic"中也可以添加在"config_name"指向的配置中；注意当有同名参数同时存在于两个未知时，"submonitor_dic"中的参数将会生效。例如spider.json中就在部分"submonitor_dic"子监视器信息中额外指定了"interval"的值，以便让这些监视有更短的检测间隔。  

#### 子监视器详解
  * 
__子监视器类名__|作用|__通用必选参数__|vip_dic匹配内容|word_dic匹配内容|cookies作用|__特有可选参数__|说明
:---|:---|:---|:---|:---|:---|:---|:---
Monitor|作为基本监视器管理子监视器组|interval|||||
YoutubeLive|监视youtube直播和视频|interval、vip_dic、word_dic、cookies、proxy、push_dic|target|标题、简介|可留空|"standby_chat"="True"/"False"，"standby_chat_onstart"="True"/"False"|standby_chat为是否检测待机直播间的弹幕 默认为"True"，standby_chat_onstart是否检测在第一次检测时已开启的待机直播间的弹幕 默认为"False"
YoutubeChat|监视youtube直播评论|同上|父监视器target（取负）、直播评论发送频道|直播评论文字|||通常由YoutubeLive监视器创建 无需在配置文件中指定
YoutubeCom|监视youtube社区帖子|同上|target|帖子文字|付费帖子，可留空|||
YoutubeNote|监视cookies对应用户的通知|同上||通知文字内容（包括superchat）|用户通知，必要|||
TwitterUser|监视twitter用户基本信息|同上|target||必要|"no_increase"="True"/"False"|no_increase为是否不推送推文和媒体数量的增加 默认为"False"
TwitterTweet|监视twitter用户的推文|同上|target、推文@对象|推文文字（包括#、@和链接）|必要|||
TwitterSearch|监视推特搜索结果|同上|target、推文@对象|推文文字（包括#、@和链接）|必要|"only_live"="True"/"False", "only_liveorvideo"="True"/"False"|only_live为是否只推送有链接指向正在进行的youtube直播的推文 默认为"False"，only_liveorvideo为是否只推送有链接指向youtube直播或视频的推文 默认为"False"，当两者同时开启时则only_liveorvideo生效
TwitcastLive|监视twitcast直播|同上|target|标题|可留空|||
TwitcastChat|监视twitcast直播评论|同上|父监视器target（取负）、直播评论发送频道|直播评论文字|||通常由TwitcastLive监视器创建 无需在配置文件中指定
FanboxUser|监视fanbox用户基本信息|同上|target||可留空|||
FanboxPost|监视fanbox用户帖子|同上|target|帖子文字|付费帖子，可留空|||
BilibiliLive|监视bilibili直播|同上|target|标题|可留空||"offline_chat"="True"/"False"，"simple_mode"="True"/"False"|offline_chat为是否监测离线直播间的弹幕 默认为"False"，simple_mode为只推送弹幕文字 默认为"False"
BilibiliChat|监视bilibili直播评论|同上|父监视器target（取负）、直播评论发送频道|直播评论文字|||通常由BilibiliLive监视器创建 无需在配置文件中指定

  * YoutubeChat和TwitcastChat子监视器会对直播评论发送者和评论内容进行关键词匹配（用于监视本人出现在其他人的直播间或者其他直播提到特定内容的情况）。为了防止vip本人的直播中的评论触发推送，如果子监视器的"target"项和"vip_dic"中关键词匹配的话，推送的"推送色彩"将会减去相应关键词的"推送色彩"。为了防止某场直播中的出现大量评论频繁触发推送，每次推送时如果"推送色彩"中如果有大于0的项，那么后续推送中这种色彩将会被增加1的推送惩罚；当色彩名字中含有"vip"字样时，这种色彩不会受到推送惩罚。
  
## 想做的事
  * 添加bilibili动态监视器。
