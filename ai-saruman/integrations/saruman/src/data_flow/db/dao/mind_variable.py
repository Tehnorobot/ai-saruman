from data_flow.db.dao.base import BaseDAO
from data_flow.db.models.mind_variable import MindVariable


class MindVariableDAO(BaseDAO):
    model = MindVariable
        
    @classmethod
    def get_by_id(cls, session, id_):
        return super().get_by_id(session, id_)