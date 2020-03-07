# -*- coding: utf-8 -*-
from nonebot import on_request, RequestSession, on_notice, NoticeSession, on_command, CommandSession
import json


def checkpause(pause_json, type, id, pausepower=None):
    is_inpause = None
    for i in range(len(pause_json)):
        if pause_json[i]['type'] == type and pause_json[i]['id'] == id:
            is_inpause = i

    if pausepower is not None:
        # 修改
        if is_inpause is not None:
            pause_json[is_inpause]['pausepower'] = pausepower
            return pause_json
        else:
            pause_json.append({'type': type, 'id': id, 'pausepower': pausepower})
            return pause_json
    else:
        # 查询
        if is_inpause is not None:
            return pause_json[is_inpause]['pausepower']
        else:
            return None

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
                pause_json = json.load(f)
            pause_json = checkpause(pause_json, 'qq_user', user_id, pausepower)
            with open('./pause.json', 'w', encoding='utf-8') as f:
                json.dump(pause_json, f)
            await session.send('当前%s(user)的暂停力度设置为%s' % (user_id, pausepower))
        if message_type == 'group' and sub_type == 'normal':
            group_id = str(session.ctx['group_id'])
            pausepower = int(session.current_arg)
            with open('./pause.json', 'r', encoding='utf-8') as f:
                pause_json = json.load(f)
            pause_json = checkpause(pause_json, 'qq_group', user_id, pausepower)
            with open('./pause.json', 'w', encoding='utf-8') as f:
                json.dump(pause_json, f)
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
            pause_json = json.load(f)
        pausepower = checkpause(pause_json, 'qq_user', user_id)
        if pausepower is not None:
            await session.send('当前%s(user)的暂停力度为%s' % (user_id, pausepower))
        else:
            await session.send('当前%s(user)尚未设置暂停力度' % user_id)
    if message_type == 'group' and sub_type == 'normal':
        group_id = str(session.ctx['group_id'])
        with open('./pause.json', 'r', encoding='utf-8') as f:
            pause_json = json.load(f)
        pausepower = checkpause(pause_json, 'qq_user', user_id)
        if pausepower is not None:
            await session.send('当前%s(group)的暂停力度为%s' % (group_id, pausepower))
        else:
            await session.send('当前%s(group)尚未设置暂停力度' % group_id)
