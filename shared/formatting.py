from discord import Member, User


class Formatting:
    @staticmethod
    def member_name_id(member: Member | User):
        return f"{member} ({member.id})"