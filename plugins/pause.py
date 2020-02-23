# -*- coding: utf-8 -*-
from nonebot import on_request, RequestSession, on_notice, NoticeSession, on_command, CommandSession
import json


@on_command('pause', aliases=('pause',), only_to_me=False)
async def pause(session: CommandSession):
    message_type = session.ctx['message_type']
    sub_type = session.ctx['sub_type']
    user_id = str(session.ctx['user_id'])
    print("[%s-%s] %s：%s" % (message_type, sub_type, user_id, session.current_arg))
    try:
        pausepower = int(session.current_arg)
        if message_type == 'private' and sub_type == 'friend':
            with open('./pause.json', 'r', encoding='utf-8') as f:
                pause = json.load(f)
            pause["pauseqq"][user_id] = pausepower
            with open('./pause.json', 'w', encoding='utf-8') as f:
                json.dump(pause, f)
            await session.send('当前%s(user)的暂停力度设置为%s' % (user_id, pausepower))
        if message_type == 'group' and sub_type == 'normal':
            group_id = str(session.ctx['group_id'])
            pausepower = int(session.current_arg)
            with open('./pause.json', 'r', encoding='utf-8') as f:
                pause = json.load(f)
            pause["pauseqq"][group_id] = pausepower
            with open('./pause.json', 'w', encoding='utf-8') as f:
                json.dump(pause, f)
            await session.send('当前%s(group)的暂停力度设置为%s' % (group_id, pausepower))
    except:
        await session.send('命令出错，正确的命令为/pause 数字')


@on_command('check', aliases=('check',), only_to_me=False)
async def check(session: CommandSession):
    message_type = session.ctx['message_type']
    sub_type = session.ctx['sub_type']
    user_id = str(session.ctx['user_id'])
    print("[%s-%s] %s：%s" % (message_type, sub_type, user_id, session.current_arg))
    if message_type == 'private' and sub_type == 'friend':
        with open('./pause.json', 'r', encoding='utf-8') as f:
            pause = json.load(f)
        if user_id in pause["pauseqq"]:
            await session.send('当前%s(user)的暂停力度为%s' % (user_id, pause["pauseqq"][user_id]))
        else:
            await session.send('当前%s(user)尚未设置暂停力度' % user_id)
    if message_type == 'group' and sub_type == 'normal':
        group_id = str(session.ctx['group_id'])
        with open('./pause.json', 'r', encoding='utf-8') as f:
            pause = json.load(f)
        if group_id in pause["pauseqq"]:
            await session.send('当前%s(group)的暂停力度为%s' % (group_id, pause["pauseqq"][group_id]))
        else:
            await session.send('当前%s(group)尚未设置暂停力度' % group_id)
