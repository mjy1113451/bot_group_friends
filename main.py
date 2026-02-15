from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
from typing import Dict, Any, Optional

# ===== 插件注册 =====
@register(
    "friend_invite_manager",
    "YourName",
    "好友申请与群邀请管理插件（支持引用回复审批）",
    "1.0.0"
)
class FriendInviteManager(Star):
    def __init__(self, context: Context, config: Optional[Dict[str, Any]] = None):
        super().__init__(context)

        # 插件配置（从 _conf_schema.json 加载，WebUI 可视化编辑）
        self.config = config or {}

        # 管理员 QQ 号列表（字符串形式）
        self.admin_qq_list: list[str] = self.config.get("admin_qq_list", [])
        # bot 自己的 QQ（用于识别引用消息）
        self.bot_qq: str = self.config.get("bot_qq", "")

        # 待处理的好友申请：request_id -> 申请信息
        self.pending_friend_requests: Dict[str, dict] = {}
        # 待处理的群邀请：request_id -> 邀请信息
        self.pending_group_invites: Dict[str, dict] = {}

    # ===== 辅助方法 =====
    def _is_admin(self, user_id: str) -> bool:
        """判断是否为管理员"""
        return user_id in self.admin_qq_list

    async def _notify_admins(self, text: str, event: AstrMessageEvent):
        """私聊通知所有管理员"""
        if not self.admin_qq_list:
            logger.warning("未配置 admin_qq_list，无法通知管理员")
            return

        for admin_qq in self.admin_qq_list:
            try:
                # 使用 AstrBot 的私聊发送方式
                # event.get_platform_adapter() 返回平台适配器
                adapter = event.get_platform_adapter()
                # 构造私聊目标（不同平台字段略有差异，这里以 OneBot 为例）
                target = {
                    "type": "private",
                    "user_id": admin_qq,
                }
                await adapter.send_message(event, text, target=target)
            except Exception as e:
                logger.error(f"通知管理员 {admin_qq} 失败: {e}")

    # ===== 好友申请相关 =====
    async def _handle_friend_request(self, event: AstrMessageEvent, request_data: dict):
        """
        处理收到的好友申请。
        request_data 是 OneBot 原始事件字典，包含：
          - request_type: "friend"
          - user_id: 申请人 QQ
          - comment: 验证信息
          - flag: 用于 set_friend_add_request 的 flag
        """
        user_id = request_data.get("user_id", "")
        comment = request_data.get("comment", "")
        flag = request_data.get("flag", "")

        # 生成一个内部 request_id（可以用 flag + 时间戳）
        request_id = f"{user_id}_{flag}"

        info = {
            "request_id": request_id,
            "user_id": user_id,
            "comment": comment,
            "flag": flag,
        }
        self.pending_friend_requests[request_id] = info

        # 构造发给管理员的通知消息
        text = (
            "【好友申请】\n"
            f"申请人QQ: {user_id}\n"
            f"验证信息: {comment}\n"
            f"申请ID: {request_id}\n"
            "请【引用】本条消息并回复：\n"
            "  同意  或  拒绝"
        )
        await self._notify_admins(text, event)

    async def _reply_friend_request(
        self, event: AstrMessageEvent, request_id: str, approve: bool
    ):
        """同意 / 拒绝好友申请"""
        info = self.pending_friend_requests.get(request_id)
        if not info:
            await event.send("未找到对应的好友申请，可能已过期或已处理。")
            return

        flag = info["flag"]
        user_id = info["user_id"]

        # 调用 OneBot 的 set_friend_add_request
        # 参考：OneBot v11 规范，需要调用 set_friend_add_request API
        # AstrBot 通过 bot.call_action 调用 OneBot API
        bot = event.bot
        if not hasattr(bot, "call_action"):
            await event.send("当前平台不支持 call_action，无法处理好友申请。")
            return

        try:
            await bot.call_action(
                "set_friend_add_request",
                flag=flag,
                approve=approve,
            )
            # 更新本地状态
            self.pending_friend_requests.pop(request_id, None)

            if approve:
                await event.send(f"已同意好友申请：{user_id}")
            else:
                await event.send(f"已拒绝好友申请：{user_id}")
        except Exception as e:
            logger.error(f"处理好友申请失败: {e}")
            await event.send(f"处理好友申请失败: {e}")

    # ===== 群邀请相关 =====
    async def _handle_group_invite(self, event: AstrMessageEvent, request_data: dict):
        """
        处理收到群邀请。
        request_data 为 OneBot 的 request 事件：
          - request_type: "group"
          - sub_type: "invite"
          - group_id: 群号
          - user_id: 邀请人 QQ
          - flag: 用于 set_group_add_request 的 flag
        """
        group_id = request_data.get("group_id", "")
        user_id = request_data.get("user_id", "")
        flag = request_data.get("flag", "")

        request_id = f"{group_id}_{user_id}_{flag}"

        info = {
            "request_id": request_id,
            "group_id": group_id,
            "user_id": user_id,
            "flag": flag,
        }
        self.pending_group_invites[request_id] = info

        text = (
            "【群邀请】\n"
            f"群号: {group_id}\n"
            f"邀请人QQ: {user_id}\n"
            f"邀请ID: {request_id}\n"
            "请【引用】本条消息并回复：\n"
            "  同意  或  拒绝"
        )
        await self._notify_admins(text, event)

    async def _reply_group_invite(
        self, event: AstrMessageEvent, request_id: str, approve: bool
    ):
        """同意 / 拒绝群邀请"""
        info = self.pending_group_invites.get(request_id)
        if not info:
            await event.send("未找到对应的群邀请，可能已过期或已处理。")
            return

        group_id = info["group_id"]
        flag = info["flag"]
        user_id = info["user_id"]

        bot = event.bot
        if not hasattr(bot, "call_action"):
            await event.send("当前平台不支持 call_action，无法处理群邀请。")
            return

        try:
            # 调用 OneBot 的 set_group_add_request
            # 参考：AstrBot 插件示例中调用 set_group_add_request
            await bot.call_action(
                "set_group_add_request",
                flag=flag,
                sub_type="add",        # 群邀请类型为 add
                approve=approve,
            )
            self.pending_group_invites.pop(request_id, None)

            if approve:
                await event.send(f"已同意群邀请：{group_id}，bot 将加入该群。")
            else:
                await event.send(f"已拒绝群邀请：{group_id}，bot 不会加入该群。")
        except Exception as e:
            logger.error(f"处理群邀请失败: {e}")
            await event.send(f"处理群邀请失败: {e}")

    # ===== 删除好友 / 拉黑 =====
    async def _delete_friend(self, event: AstrMessageEvent, user_id: str):
        """删除好友"""
        bot = event.bot
        if not hasattr(bot, "call_action"):
            await event.send("当前平台不支持 call_action，无法删除好友。")
            return

        try:
            # OneBot v11: delete_friend API
            await bot.call_action(
                "delete_friend",
                user_id=user_id,
            )
            await event.send(f"已删除好友：{user_id}")
        except Exception as e:
            logger.error(f"删除好友失败: {e}")
            await event.send(f"删除好友失败: {e}")

    async def _ban_user(self, event: AstrMessageEvent, user_id: str):
        """拉黑用户（加入黑名单）"""
        bot = event.bot
        if not hasattr(bot, "call_action"):
            await event.send("当前平台不支持 call_action，无法拉黑用户。")
            return

        try:
            # OneBot v11: set_friend_blacklist API（具体字段以平台适配器为准）
            # 这里给出一个典型调用方式，实际使用请根据 OneBot 实现调整
            await bot.call_action(
                "set_friend_blacklist",
                user_id=user_id,
                enable=True,   # True 为拉黑，False 为解除拉黑
            )
            await event.send(f"已拉黑用户：{user_id}")
        except Exception as e:
            logger.error(f"拉黑用户失败: {e}")
            await event.send(f"拉黑用户失败: {e}")

    # ===== 事件监听 =====
    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_all_message(self, event: AstrMessageEvent):
        """
        拦截所有消息，处理：
        1. 管理员引用好友申请/群邀请通知的回复
        2. 管理员发送的「删除好友 / 拉黑」指令
        """
        # 只处理管理员的消息
        sender_id = event.get_sender_id()
        if not self._is_admin(sender_id):
            return

        # 获取消息文本
        msg: str = event.message_str.strip()
        if not msg:
            return

        # ===== 1. 处理引用回复（好友申请 / 群邀请） =====
        # AstrBot 文档中 message_obj.message 为消息链
        # 这里简单假设引用消息会带有 "【好友申请】" 或 "【群邀请】" 标识
        quoted_text = ""
        if hasattr(event, "message_obj") and hasattr(event.message_obj, "message"):
            # 遍历消息链，找到引用的文本（简化写法）
            for comp in event.message_obj.message:
                # 实际项目中需要根据平台适配器的引用消息格式解析
                # 这里只做示例：假设 Plain 组件中包含引用文本
                if hasattr(comp, "text"):
                    quoted_text = comp.text
                    break

        # 如果引用的是“好友申请”通知
        if "【好友申请】" in quoted_text:
            # 从引用文本中提取申请ID
            # 假设通知中有 "申请ID: xxx" 这一行
            lines = quoted_text.splitlines()
            request_id = None
            for line in lines:
                if line.startswith("申请ID:"):
                    request_id = line.split(":", 1)[1].strip()
                    break

            if request_id and request_id in self.pending_friend_requests:
                if "同意" in msg:
                    await self._reply_friend_request(event, request_id, True)
                    return
                elif "拒绝" in msg:
                    await self._reply_friend_request(event, request_id, False)
                    return

        # 如果引用的是“群邀请”通知
        if "【群邀请】" in quoted_text:
            lines = quoted_text.splitlines()
            request_id = None
            for line in lines:
                if line.startswith("邀请ID:"):
                    request_id = line.split(":", 1)[1].strip()
                    break

            if request_id and request_id in self.pending_group_invites:
                if "同意" in msg:
                    await self._reply_group_invite(event, request_id, True)
                    return
                elif "拒绝" in msg:
                    await self._reply_group_invite(event, request_id, False)
                    return

        # ===== 2. 处理普通指令（删除好友 / 拉黑） =====
        # 「删除好友 123456」
        if msg.startswith("删除好友 "):
            parts = msg.split(maxsplit=1)
            if len(parts) == 2:
                user_id = parts[1].strip()
                await self._delete_friend(event, user_id)
                return

        # 「拉黑 123456」
        if msg.startswith("拉黑 "):
            parts = msg.split(maxsplit=1)
            if len(parts) == 2:
                user_id = parts[1].strip()
                await self._ban_user(event, user_id)
                return

    # ===== 处理好友申请 / 群邀请事件（OneBot 示例） =====
    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_request_event(self, event: AstrMessageEvent):
        """
        处理 OneBot 的 request 事件（好友申请、群邀请等）。
        参考：AstrBot 群聊申请审核插件示例中判断 post_type == "request" 的写法。
        """
        if not hasattr(event, "message_obj") or not hasattr(event.message_obj, "raw_message"):
            return

        raw_message = event.message_obj.raw_message
        if not isinstance(raw_message, dict):
            return

        # 判断是否为请求事件
        if raw_message.get("post_type") != "request":
            return

        request_type = raw_message.get("request_type")
        sub_type = raw_message.get("sub_type")

        # 好友申请
        if request_type == "friend":
            await self._handle_friend_request(event, raw_message)

        # 群邀请
        if request_type == "group" and sub_type == "invite":
            await self._handle_group_invite(event, raw_message)

    # ===== 插件生命周期 =====
    async def terminate(self):
        """插件被停用/卸载时调用"""
        logger.info("好友申请与群邀请管理插件已停用")
