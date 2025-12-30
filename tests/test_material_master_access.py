import unittest

from sqlalchemy import select
from sqlalchemy.dialects import postgresql

from bot.app.repository.materials import MaterialsRepository
from shared.models import Material


class TestMaterialMasterAccessFilter(unittest.TestCase):
    def test_master_access_filter_sql_contains_exists_or(self):
        expr = MaterialsRepository._master_access_filter(user_id=123)
        q = select(Material.id).where(expr)
        sql = str(q.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))

        s = sql.lower()
        self.assertIn("exists", s)
        self.assertIn(" or ", s)
        self.assertIn("not", s)
        self.assertIn("material_master_access", s)
        self.assertIn("user_id = 123", s)


if __name__ == "__main__":
    unittest.main()
