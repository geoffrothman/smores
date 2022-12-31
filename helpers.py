from db import models, database
from slack_sdk import WebClient
from typing import List, Tuple


def generate_member_model_list(member_ids, exclude_members, channel_id, team_id):
    members = []
    for member in member_ids:
        if member not in exclude_members:
            members.append(
                models.ChannelMembers(channel_id=channel_id, member_id=member, team_id=team_id)
            )

    return members


def get_slack_client(enterprise_id: str | None, team_id: str) -> WebClient:
    installation = database.installation_store.find_installation(
        enterprise_id=enterprise_id, team_id=team_id
    )

    return WebClient(token=installation.bot_token)


def round_robin_match(members: List) -> Tuple[List[List], List]:
    count = len(members)
    midpoint = count // 2
    if count % 2 != 0:
        raise ValueError("matching is supported for even number of members only")

    members_rotated_circle = members[0:1] + members[-1:] + members[1:-1]

    pairs = []
    for i in range(midpoint):
        if i >= midpoint:
            break

        pairs.append([members_rotated_circle[i], members_rotated_circle[count - 1 - i]])

    return pairs, members_rotated_circle
