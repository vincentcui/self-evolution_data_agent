"""导入数据源和 schema"""
import asyncio, httpx

BASE = "http://localhost:8002"

async def main():
    async with httpx.AsyncClient() as c:
        # 1. 登录
        r = await c.post(f"{BASE}/api/auth/login", json={"username": "admin", "password": "admin123456"})
        token = r.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        # 2. 检查是否已有命名空间
        r = await c.get(f"{BASE}/api/namespaces", headers=headers)
        namespaces = r.json()
        target = next((ns for ns in namespaces if ns["slug"] == "ecommerce"), None)
        if target:
            ns_id = target["id"]
            print(f"使用已有命名空间: {target['name']} (id={ns_id})")
        else:
            r = await c.post(f"{BASE}/api/namespaces", json={
                "name": "电商", "slug": "ecommerce", "description": "电商数据库"
            }, headers=headers)
            target = r.json()
            ns_id = target["id"]
            print(f"创建命名空间: {target['name']} (id={ns_id})")

        # 3. 检查是否已有数据源
        r = await c.get(f"{BASE}/api/namespaces/{ns_id}/datasources", headers=headers)
        datasources = r.json()
        if datasources:
            print(f"已有 {len(datasources)} 个数据源")
            for ds in datasources:
                print(f"  id={ds['id']} {ds.get('db_type')}://{ds.get('host')}/{ds.get('database')}")
        else:
            r = await c.post(f"{BASE}/api/namespaces/{ns_id}/datasources", json={
                "db_type": "mysql", "host": "localhost", "port": 3306,
                "database": "ecommerce", "username": "root", "password": "root123",
                "timezone": "Asia/Shanghai",
            }, headers=headers)
            if r.status_code >= 400:
                print(f"数据源创建失败: {r.status_code} {r.text[:300]}")
                return
            ds = r.json()
            print(f"创建数据源: {ds.get('db_type')}://{ds.get('host')}/{ds.get('database')} (id={ds['id']})")

asyncio.run(main())
