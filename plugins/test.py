# -*- coding: utf-8 -*-
from nonebot import on_request, RequestSession, on_notice, NoticeSession, on_command, CommandSession
from nonebot import on_natural_language, NLPSession, IntentCommand
import re



@on_command('test', aliases=('test',))
async def test(session: CommandSession):
    await session.send('我在')

@on_command('echo', aliases=('echo',))
async def echo(session: CommandSession):
    await session.send(session.current_arg)

@on_natural_language(keywords={'在吗'})
async def _(session: NLPSession):
    return IntentCommand(90.0, 'test')

@on_natural_language(keywords={'复读'})
async def _(session: NLPSession):
    text=re.sub('复读', '', session.msg_text, 1)
    return IntentCommand(90.0, 'echo', current_arg=text)
