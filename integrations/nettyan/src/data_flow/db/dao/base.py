from sqlalchemy.orm import Session


class BaseDAO:
    """Базовый DAO с классовыми методами для CRUD операций."""
    model = None  # потомки должны указать свою модель

    @classmethod
    def create(cls, session: Session, **kwargs):
        """Создать новую запись."""
        obj = cls.model(**kwargs)
        session.add(obj)
        session.commit()
        session.refresh(obj)
        return obj

    @classmethod
    def get_by_id(cls, session: Session, id_):
        """Получить запись по ID."""
        return session.get(cls.model, id_)

    @classmethod
    def get_all(cls, session: Session, limit=None, offset=None):
        """Получить все записи."""
        query = session.query(cls.model)
        if offset:
            query = query.offset(offset)
        if limit:
            query = query.limit(limit)
        return query.all()

    @classmethod
    def filter_by(cls, session: Session, is_list = True, **kwargs):
        """Получить записи по фильтру."""
        if is_list:
            return session.query(cls.model).filter_by(**kwargs).all()
        return session.query(cls.model).filter_by(**kwargs).first()

    @classmethod
    def update(cls, session: Session, id_, **kwargs):
        """Обновить запись по ID."""
        obj = cls.get_by_id(session, id_)
        if not obj:
            return None
        for key, value in kwargs.items():
            setattr(obj, key, value)
        session.commit()
        session.refresh(obj)
        return obj

    @classmethod
    def delete(cls, session: Session, id_):
        """Удалить запись по ID."""
        obj = cls.get_by_id(session, id_)
        if not obj:
            return False
        session.delete(obj)
        session.commit()
        return True