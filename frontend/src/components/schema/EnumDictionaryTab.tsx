/**
 * EnumDictionaryTab — 枚举字典子 Tab
 *
 * 列表展示 namespace 下所有 EnumDictionary, 支持搜索/新建/编辑/删除
 */
import { useState, useEffect, useCallback } from "react";
import { Table, Button, Tag, Space, Popconfirm, Input, message } from "antd";
import { EnumEditorModal } from "./EnumEditorModal";
import { enumApi } from "@/api";
import type { EnumCanonical } from "@/types/schema-canonical";
import { enumDictSourceLabel } from "./sourceLabels";

interface Props {
  namespaceId: number;
  dbType: import("@/types").DbType;
}

export function EnumDictionaryTab({ namespaceId, dbType }: Props) {
  const [items, setItems] = useState<EnumCanonical[]>([]);
  const [loading, setLoading] = useState(false);
  const [editorOpen, setEditorOpen] = useState(false);
  const [editing, setEditing] = useState<EnumCanonical | null>(null);
  const [filter, setFilter] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await enumApi.listEnumDictionaries({
        namespace_id: namespaceId,
        ...(filter ? { name_like: filter } : {}),
      });
      setItems(data.items);
    } catch {
      message.error("加载枚举字典失败");
    } finally {
      setLoading(false);
    }
  }, [namespaceId, filter]);

  useEffect(() => {
    void load();
  }, [load]);

  const handleDelete = async (id: number) => {
    try {
      await enumApi.deleteEnumCanonical(id, { dryRun: false });
      message.success("已删除");
      void load();
    } catch {
      message.error("删除失败");
    }
  };

  return (
    <div>
      <Space style={{ marginBottom: 12 }}>
        <Input.Search
          placeholder="搜索 enum 名称"
          onSearch={(v) => setFilter(v)}
          allowClear
          style={{ width: 240 }}
        />
        <Button type="primary" onClick={() => { setEditing(null); setEditorOpen(true); }}>
          新建枚举
        </Button>
      </Space>
      <Table
        rowKey="id"
        loading={loading}
        dataSource={items}
        pagination={{ pageSize: 20 }}
        size="small"
        columns={[
          { title: "名称", dataIndex: "enum_class_name" },
          { title: "values 数", render: (_, r) => r.values.length, width: 90 },
          {
            title: "来源",
            dataIndex: "source",
            width: 80,
            render: (s: string) => <Tag color={s === "manual" ? "blue" : "green"}>{enumDictSourceLabel(s)}</Tag>,
          },
          { title: "引用字段", dataIndex: "reference_count", width: 90, render: (n) => n ?? "—" },
          {
            title: "操作",
            width: 140,
            render: (_, r) => (
              <Space>
                <Button size="small" onClick={() => { setEditing(r); setEditorOpen(true); }}>
                  编辑
                </Button>
                <Popconfirm
                  title="删除将级联解绑所有引用字段, 确认?"
                  onConfirm={() => handleDelete(r.id)}
                >
                  <Button size="small" danger>删除</Button>
                </Popconfirm>
              </Space>
            ),
          },
        ]}
      />
      <EnumEditorModal
        open={editorOpen}
        mode={editing ? "edit" : "create"}
        namespaceId={namespaceId}
        dbType={dbType}
        initial={
          editing
            ? { enum_class_name: editing.enum_class_name, values: editing.values }
            : undefined
        }
        onClose={() => setEditorOpen(false)}
        onSubmit={async (payload) => {
          if (editing) {
            await enumApi.updateEnumCanonical(editing.id, { values: payload.values });
          } else {
            await enumApi.createEnumDictionary({
              namespace_id: namespaceId,
              db_type: dbType,
              enum_class_name: payload.enum_class_name,
              values: payload.values,
              comment: payload.comment,
            });
          }
          void load();
        }}
      />
    </div>
  );
}
