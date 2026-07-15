from piltover.enums import ReqHandlerFlags
from piltover.exceptions import ErrorRpc
from piltover.tl import WebPageEmpty, AttachMenuBots, EmojiKeywordsDifference, \
    PeerSettings, TLObjectVector, InputReportReasonSpam, InputReportReasonViolence, \
    InputReportReasonPornography, InputReportReasonChildAbuse, InputReportReasonCopyright, \
    InputReportReasonFake, InputReportReasonIllegalDrugs, InputReportReasonPersonalDetails, \
    InputReportReasonOther, MessageReportOption, ReportResultChooseOption, ReportResultAddComment, \
    ReportResultReported
from piltover.tl.base.channels import SponsoredMessageReportResult
from piltover.tl.functions.channels import GetSponsoredMessages_133
from piltover.tl.functions.messages import GetPeerSettings, GetQuickReplies, GetMessageEditData, \
    GetEmojiKeywordsLanguages, GetWebPage, GetTopReactions, GetAttachMenuBots, \
    GetStickers, GetSuggestedDialogFilters, GetSavedReactionTags, \
    GetFeaturedStickers, GetFeaturedEmojiStickers, GetEmojiKeywords, GetWebPagePreview, GetDefaultTagReactions, \
    GetEmojiKeywordsDifference, GetAvailableEffects, GetSponsoredMessages, ReportSponsoredMessage, ViewSponsoredMessage, \
    ClickSponsoredMessage, TranscribeAudio, RateTranscribedAudio, Report
from piltover.tl.types.channels import SponsoredMessageReportResultReported
from piltover.tl.types.messages import PeerSettings as MessagesPeerSettings, Reactions, SavedReactionTags, \
    Stickers, FeaturedStickers, MessageEditData, \
    QuickReplies, AvailableEffects, SponsoredMessages, SponsoredMessagesEmpty, TranscribedAudio
from piltover.worker import MessageHandler

handler = MessageHandler("messages.stubs")

_REPORT_OPTIONS = [
    ("Spam", InputReportReasonSpam()),
    ("Violence", InputReportReasonViolence()),
    ("Pornography", InputReportReasonPornography()),
    ("Child Abuse", InputReportReasonChildAbuse()),
    ("Copyright", InputReportReasonCopyright()),
    ("Scam or fraud", InputReportReasonFake()),
    ("Illegal goods", InputReportReasonIllegalDrugs()),
    ("Personal data", InputReportReasonPersonalDetails()),
    ("Other", InputReportReasonOther()),
]
_OTHER_REPORT_OPTION = InputReportReasonOther().write()


@handler.on_request(GetPeerSettings, ReqHandlerFlags.AUTH_NOT_REQUIRED)
async def get_peer_settings():  # pragma: no cover
    return MessagesPeerSettings(
        settings=PeerSettings(),
        chats=[],
        users=[],
    )


@handler.on_request(GetEmojiKeywordsLanguages, ReqHandlerFlags.AUTH_NOT_REQUIRED)
async def get_emoji_keywords_languages():  # pragma: no cover
    return TLObjectVector()


@handler.on_request(GetWebPage, ReqHandlerFlags.AUTH_NOT_REQUIRED)
async def get_web_page():  # pragma: no cover
    return WebPageEmpty(id=0)


@handler.on_request(GetTopReactions, ReqHandlerFlags.AUTH_NOT_REQUIRED)
async def get_top_reactions():  # pragma: no cover
    return Reactions(hash=0, reactions=[])


@handler.on_request(GetAttachMenuBots, ReqHandlerFlags.AUTH_NOT_REQUIRED)
async def get_attach_menu_bots():  # pragma: no cover
    return AttachMenuBots(
        hash=0,
        bots=[],
        users=[],
    )


@handler.on_request(GetStickers, ReqHandlerFlags.AUTH_NOT_REQUIRED)
async def get_stickers():  # pragma: no cover
    return Stickers(hash=0, stickers=[])


@handler.on_request(GetSuggestedDialogFilters, ReqHandlerFlags.AUTH_NOT_REQUIRED)
async def get_suggested_dialog_filters():  # pragma: no cover
    return TLObjectVector()


@handler.on_request(GetFeaturedStickers, ReqHandlerFlags.AUTH_NOT_REQUIRED)
@handler.on_request(GetFeaturedEmojiStickers, ReqHandlerFlags.AUTH_NOT_REQUIRED)
async def get_featured_stickers():  # pragma: no cover
    return FeaturedStickers(
        hash=0,
        count=0,
        sets=[],
        unread=[],
    )


@handler.on_request(GetEmojiKeywords, ReqHandlerFlags.AUTH_NOT_REQUIRED)
async def get_emoji_keywords(request: GetEmojiKeywords):  # pragma: no cover
    return EmojiKeywordsDifference(lang_code=request.lang_code, from_version=0, version=0, keywords=[])


@handler.on_request(GetWebPagePreview, ReqHandlerFlags.AUTH_NOT_REQUIRED)
async def get_webpage_preview():  # pragma: no cover
    return WebPageEmpty(id=0)


@handler.on_request(GetMessageEditData, ReqHandlerFlags.AUTH_NOT_REQUIRED)
async def get_message_edit_data():  # pragma: no cover
    return MessageEditData(caption=True)


@handler.on_request(GetQuickReplies, ReqHandlerFlags.AUTH_NOT_REQUIRED)
async def get_quick_replies() -> QuickReplies:  # pragma: no cover
    return QuickReplies(
        quick_replies=[],
        messages=[],
        chats=[],
        users=[],
    )


@handler.on_request(GetDefaultTagReactions, ReqHandlerFlags.AUTH_NOT_REQUIRED)
async def get_default_tag_reactions() -> Reactions:  # pragma: no cover
    return Reactions(
        hash=0,
        reactions=[],
    )


@handler.on_request(GetSavedReactionTags, ReqHandlerFlags.AUTH_NOT_REQUIRED)
async def get_saved_reaction_tags() -> SavedReactionTags:  # pragma: no cover
    return SavedReactionTags(
        tags=[],
        hash=0,
    )


@handler.on_request(GetEmojiKeywordsDifference, ReqHandlerFlags.AUTH_NOT_REQUIRED)
async def get_emoji_keywords_difference(
        request: GetEmojiKeywordsDifference,
) -> EmojiKeywordsDifference:  # pragma: no cover
    return EmojiKeywordsDifference(
        lang_code=request.lang_code,
        from_version=request.from_version,
        version=request.from_version,
        keywords=[],
    )


@handler.on_request(GetAvailableEffects, ReqHandlerFlags.AUTH_NOT_REQUIRED)
async def get_available_effects() -> AvailableEffects:  # pragma: no cover
    return AvailableEffects(
        hash=0,
        effects=[],
        documents=[],
    )


@handler.on_request(GetSponsoredMessages, ReqHandlerFlags.DONT_FETCH_USER)
async def get_sponsored_messages(
        request: GetSponsoredMessages, user_id: int,
) -> SponsoredMessages | SponsoredMessagesEmpty:
    from piltover.app.utils.test_sponsored_messages import build_channel_sponsored_messages
    from piltover.db.models import Channel
    from piltover.tl import InputPeerChannel

    if not isinstance(request.peer, InputPeerChannel):
        return SponsoredMessagesEmpty()

    channel = await Channel.get_from_input(user_id, request.peer)
    if channel is None or not channel.channel:
        return SponsoredMessagesEmpty()

    return await build_channel_sponsored_messages(channel)


@handler.on_request(Report, ReqHandlerFlags.BOT_NOT_ALLOWED | ReqHandlerFlags.DONT_FETCH_USER)
async def report_message(
        request: Report, user_id: int,
) -> ReportResultChooseOption | ReportResultAddComment | ReportResultReported:
    if not request.id:
        raise ErrorRpc(error_code=400, error_message="MESSAGE_REQUIRED")

    if not request.option:
        return ReportResultChooseOption(
            title="What is wrong with this message?",
            options=[
                MessageReportOption(text=text, option=reason.write())
                for text, reason in _REPORT_OPTIONS
            ],
        )

    if request.option == _OTHER_REPORT_OPTION and not request.message:
        return ReportResultAddComment(optional=True, option=request.option)

    from piltover.app.utils.admin_reports import create_admin_report
    from piltover.db.enums import AdminReportPeerType, PeerType
    from piltover.db.models import Peer

    peer = await Peer.from_input_peer_raise(user_id, request.peer)
    if peer.type is PeerType.USER:
        peer_type, peer_id = AdminReportPeerType.USER, peer.user_id
    elif peer.type is PeerType.CHAT:
        peer_type, peer_id = AdminReportPeerType.CHAT, peer.chat_id
    elif peer.type is PeerType.CHANNEL:
        peer_type, peer_id = AdminReportPeerType.CHANNEL, peer.channel_id
    else:
        return ReportResultReported()

    reason = "other"
    for label, reason_obj in _REPORT_OPTIONS:
        if request.option == reason_obj.write():
            reason = label.lower().replace(" ", "_")
            break

    await create_admin_report(
        reporter_id=user_id,
        peer_type=peer_type,
        peer_id=peer_id,
        reason=reason,
        comment=request.message or None,
        message_ids=list(request.id),
    )
    return ReportResultReported()


@handler.on_request(ReportSponsoredMessage, ReqHandlerFlags.AUTH_NOT_REQUIRED)
async def report_sponsored_message() -> SponsoredMessageReportResult:  # pragma: no cover
    return SponsoredMessageReportResultReported()


@handler.on_request(ClickSponsoredMessage, ReqHandlerFlags.AUTH_NOT_REQUIRED)
async def click_sponsored_message() -> bool:  # pragma: no cover
    return True


@handler.on_request(ViewSponsoredMessage, ReqHandlerFlags.AUTH_NOT_REQUIRED)
async def view_sponsored_message() -> bool:  # pragma: no cover
    return True


@handler.on_request(TranscribeAudio)
async def transcribe_audio() -> TranscribedAudio:  # pragma: no cover
    return TranscribedAudio(transcription_id=0, text="ку")


@handler.on_request(RateTranscribedAudio)
async def rate_transcribed_audio() -> bool:  # pragma: no cover
    return True
