/**
 * EnumBindDrawer — 字段绑定 enum 抽屉
 *
 * 展示当前字段样本值, 选择 enum (支持搜索), 校验覆盖率, 绑定/强制绑定
 */
import { useEffect, useState } from "react";
import { Drawer, Select, Button, Alert, Modal, Space, Tag } from "antd";
import { enumApi } from "@/api";
import type { EnumCanonical } from "@/types/schema-canonical";
import { enumDictSourceLabel } from "./sourceLabels";

interface Props {
  open: boolean;
  collectionId: number;
  fieldName: string;
  fieldType: string;
  namespaceId: number;
  samples?: (number | string)[];
  /** 当前已绑定的 enum 字典 id —— “变更”时预回填到选择器 */
  currentEnumId?: number | null;
  onClose: () => void;
  onBound: () => void;
}

export function EnumBindDrawer({
  open,
  collectionId,
  fieldName,
  fieldType,
  namespaceId,
  samples,
  currentEnumId,
  onClose,
  onBound,
}: Props) {
  const [enums, setEnums] = useState<EnumCanonical[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setSelectedId(currentEnumId ?? null);
    setError(null);
    enumApi
      .listEnumDictionaries({ namespace_id: namespaceId })
      .then((d) => setEnums(d.items))
      .catch(() => setEnums([]));
  }, [open, namespaceId, currentEnumId]);

  const selectedEnum = enums.find((e) => e.id === selectedId);
  const enumValues = selectedEnum?.values.map((v) => v.db_value) ?? [];
  const notCovered = selectedEnum
    ? (samples ?? []).filter((s) => !enumValues.includes(s))
    : [];

  const submit = async (force: boolean) => {
    if (!selectedId) return;
    setSubmitting(true);
    setError(null);
    try {
      await enumApi.bindFieldEnum(namespaceId, collectionId, fieldName, {
        enum_dict_id: selectedId,
        force,
      });
      onBound();
      onClose();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSubmitting(false);
    }
  };

  const handleBind = () => {
    if (!selectedId) return;
    if (notCovered.length > 0) {
      Modal.confirm({
        title: "枚举覆盖冲突",
        content: (
          <div>
            <p>以下样本值不在枚举值集合中:</p>
            <div>
              {notCovered.map((v) => (
                <Tag key={String(v)} color="red">{String(v)}</Tag>
              ))}
            </div>
            <p style={{ marginTop: 8 }}>强制绑定将进入 conflict 队列，需人工 resolve。</p>
          </div>
        ),
        okText: "强制绑定",
        okButtonProps: { danger: true },
        cancelText: "取消",
        onOk: () => submit(true),
      });
    } else {
      void submit(false);
    }
  };

  return (
    <Drawer
      title={`绑定字段 ${fieldName} (${fieldType})`}
      open={open}
      onClose={onClose}
      width={520}
    >
      <Space direction="vertical" style={{ width: "100%" }} size="middle">
        {samples && samples.length > 0 && (
          <div>
            <strong>样本值:</strong>{" "}
            {samples.map((s) => (
              <Tag key={String(s)}>{String(s)}</Tag>
            ))}
          </div>
        )}

        <Select
          placeholder="选择 enum"
          style={{ width: "100%" }}
          value={selectedId}
          onChange={setSelectedId}
          showSearch
          filterOption={(input, option) =>
            (option?.label ?? "").toLowerCase().includes(input.toLowerCase())
          }
          options={enums.map((e) => ({
            value: e.id,
            label: `${e.enum_class_name} (${e.values.length} 个值, 来源: ${enumDictSourceLabel(e.source)})`,
          }))}
        />

        {selectedEnum && (
          <div>
            <strong>候选值:</strong>{" "}
            {selectedEnum.values.map((v) => (
              <Tag key={String(v.db_value)} color="blue">
                {v.db_value} = {v.name}
              </Tag>
            ))}
          </div>
        )}

        {notCovered.length > 0 && (
          <Alert
            type="warning"
            message={`样本 ${JSON.stringify(notCovered)} 未在 enum 值集合中`}
            description="点击绑定将弹出确认对话框。"
          />
        )}

        {error && <Alert type="error" message={error} />}

        <Button
          type="primary"
          loading={submitting}
          disabled={!selectedId}
          onClick={handleBind}
        >
          绑定
        </Button>
      </Space>
    </Drawer>
  );
}
