import logging
import random
import os
import time
import helpers

from sqlalchemy import and_
from datetime import datetime, timedelta

from db import crud, models, database
from task_runner import celery


logger = logging.getLogger(__name__)


@celery.task
def cache_channel_members(channel_id, team_id, enterprise_id):
    # TODO: If this task fails it doesn't retry, add re-try logic
    sc = helpers.get_slack_client(enterprise_id, team_id)
    with database.SessionLocal() as db:
        members_data = sc.conversations_members(channel=channel_id, limit=200).data
        local_members = crud.get_cached_channel_member_ids(db, channel_id, team_id)
        members = helpers.generate_member_model_list(
            members_data["members"], local_members, channel_id, team_id
        )

        next_cursor = members_data["response_metadata"]["next_cursor"]
        while next_cursor:
            members_data = sc.conversations_members(channel=channel_id, limit=200, cursor=next_cursor).data
            members += helpers.generate_member_model_list(
                members_data["members"], local_members, channel_id, team_id
            )

            next_cursor = members_data["response_metadata"]["next_cursor"]

        db.bulk_save_objects(members)
        db.commit()


@celery.task
def match_pairs_periodic():
    # TODO: allow configuring of which day to start conversations on per channel basis
    # TODO: If another task starts while previous one is already running that can potentially add issues, use locks to prevent that
    if datetime.now().weekday() != int(os.environ.get("CONVERSATION_DAY", 1)):
        return

    with database.SessionLocal() as db:
        while True:
            channels = crud.get_channels_eligible_for_pairing(db, 10)
            if len(channels) == 0:
                break
            for channel in channels:
                generate_and_send_conversations(channel, db)


@celery.task
def force_generate_conversations(channel_id):
    with database.SessionLocal() as db:
        channel = db.query(models.Channels).where(models.Channels.channel_id == channel_id).first()
        if channel is None:
            return

        generate_and_send_conversations(channel, db)


@celery.task
def send_failed_intros():
    with database.SessionLocal() as db:
        pending_intros = (
            db.query(models.ChannelConversations)
            .where(
                and_(
                    models.ChannelConversations.sent_on == None,
                    models.ChannelConversations.conversations["status"].astext == "PARTIALLY_SENT",
                )
            )
            .all()
        )
        for intro in pending_intros:
            enterprise_id = crud.get_enterprise_id(db, intro.team_id, intro.channel_id)
            client = helpers.get_slack_client(enterprise_id, intro.team_id)

            all_convos_sent = True
            for conv in intro.conversations["pairs"]:
                if conv["status"] != "GENERATED":
                    continue
                # TODO: handle 429 errors from slack api
                time.sleep(1.2)
                try:
                    response = client.conversations_open(users=conv["pair"])
                    client.chat_postMessage(
                        text=_intro_message(intro.channel_id),
                        channel=response.data["channel"]["id"],
                    )
                    conv["status"] = "INTRO_SENT"
                    conv["channel_id"] = response.data["channel"]["id"]
                except Exception:
                    all_convos_sent = False
                    logger.exception("error opening conversation")

            if all_convos_sent:
                intro.conversations["status"] = "INTRO_SENT"
                intro.sent_on = datetime.utcnow().date()
            else:
                intro.conversations["status"] = "PARTIALLY_SENT"

            db.commit()


@celery.task
def send_midpoint_reminder():
    with database.SessionLocal() as db:
        midpoint_convos = (
            db.query(models.ChannelConversations)
            .where(
                and_(
                    models.ChannelConversations.conversations.op("->")("midpoint_status") == None,
                    models.ChannelConversations.sent_on == datetime.utcnow().date() - timedelta(8),
                )
            )
            .all()
        )

        for intro in midpoint_convos:
            enterprise_id = crud.get_enterprise_id(db, intro.team_id, intro.channel_id)
            client = helpers.get_slack_client(enterprise_id, intro.team_id)

            all_convos_sent = True
            for conv in intro.conversations["pairs"]:
                if conv["status"] != "INTRO_SENT" or "midpoint_sent_on" in conv:
                    continue
                time.sleep(1.2)
                try:
                    client.chat_postMessage(
                        text=":wave: Mid point reminder - if you haven't met yet, make it happen!",
                        channel=conv["channel_id"],
                    )
                    conv["midpoint_sent_on"] = datetime.utcnow().date().isoformat()
                except Exception:
                    all_convos_sent = False
                    logger.exception("error sending midpoint")

            intro.conversations["midpoint_status"] = "SENT" if all_convos_sent else "PARTIALLY_SENT"
            db.commit()


def generate_and_send_conversations(channel, db):
    conv_pairs = create_conversation_pairs(channel, db)
    
    client = helpers.get_slack_client(channel.enterprise_id, channel.team_id)
    all_convos_sent = True
    for conv_pair in conv_pairs.conversations["pairs"]:
        # TODO: handle 429 errors from slack api
        time.sleep(1.2)
        try:
            response = client.conversations_open(users=conv_pair["pair"])
            client.chat_postMessage(
                text=_intro_message(channel.channel_id),
                channel=response.data["channel"]["id"],
            )
            conv_pair["status"] = "INTRO_SENT"
            conv_pair["channel_id"] = response.data["channel"]["id"]
        except Exception:
            all_convos_sent = False
            logger.exception("error opening conversation")

    channel.last_sent_on = datetime.utcnow().date()
    if all_convos_sent:
        conv_pairs.conversations["status"] = "INTRO_SENT"
        conv_pairs.sent_on = datetime.utcnow().date()
    else:
        conv_pairs.conversations["status"] = "PARTIALLY_SENT"

    db.commit()


def create_conversation_pairs(channel, db):
    members_list = crud.get_cached_channel_member_ids(db, channel.channel_id, channel.team_id)

    installation = database.installation_store.find_installation(
        enterprise_id=channel.enterprise_id, team_id=channel.team_id
    )
    
    if installation.bot_user_id in members_list:
        members_list.remove(installation.bot_user_id)
    
    if len(members_list) < 2:
        channel.last_sent_on = datetime.utcnow().date()
        db.commit()
        return []

    random.shuffle(members_list)

    count = len(members_list)
    pairs = []
    for i in range(count // 2):
        pairs.append([members_list[i], members_list[count - i - 1]])

    if count % 2 != 0:
        last_pair = pairs[len(pairs) - 1]
        last_pair.append(members_list[count // 2])
        pairs[len(pairs) - 1] = last_pair

    return crud.save_channel_conversations(db, channel, pairs)

def _intro_message(channel_id):
    return f"hello :wave:! You've been matched for a S'mores chat because you're member of <#{channel_id}>. Find some time on your calendar and make it happen!"
