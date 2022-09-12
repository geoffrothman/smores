from sqlalchemy import delete, and_
from sqlalchemy.orm import Session
from sqlalchemy.dialects.postgresql import insert

from . import models


def get_channel(db: Session, channel_id: str):
    return (
        db.query(models.Channels)
        .filter(models.Channels.channel_id == channel_id)
        .first()
    )


def add_channel(db: Session, channel_id: str, team_id: str, enterprise_id: str):
    channel = models.Channels(
        channel_id=channel_id,
        team_id=team_id,
        enterprise_id=enterprise_id,
        is_active=True,
    )
    db.add(channel)
    db.commit()
    db.refresh(channel)
    return channel


def add_member_if_not_exists(
    db: Session, member_id: str, channel_id: str, team_id: str
):
    insert_query = (
        insert(models.ChannelMembers)
        .values(member_id=member_id, channel_id=channel_id, team_id=team_id)
        .on_conflict_do_nothing(index_elements=["member_id", "channel_id", "team_id"])
    )
    result = db.execute(insert_query)
    db.commit()

    return result.rowcount


def delete_member(db: Session, member_id: str, channel_id: str, team_id: str):
    condition = [
        models.ChannelMembers.member_id == member_id,
        models.ChannelMembers.channel_id == channel_id,
        models.ChannelMembers.team_id == team_id,
    ]
    delete_query = delete(models.ChannelMembers).where(and_(*condition))
    result = db.execute(delete_query)
    db.commit()

    return result.rowcount


def get_channels(db: Session, skip: int = 0, limit: int = 100):
    return db.query(models.Channels).offset(skip).limit(limit).all()


def add_conversation(db: Session, channel_id: str, team_id: str, conversations):
    conversation = models.ChannelConversations(
        channel_id=channel_id, team_id=team_id, conversations=conversations
    )
    db.add(conversation)
    db.commit()
    db.refresh(conversation)
    return conversation
