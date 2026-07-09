import React, { useState } from "react";
import { Card, Radio, Input, Button, Space } from "antd";
import type { PendingClarify } from "@/hooks/useAgentStream";

const CUSTOM_INPUT_KEY = "__custom__";

export const ClarifyCard: React.FC<{
  pending: PendingClarify;
  onSubmit: (pendingId: number, answer: string) => void;
}> = ({ pending, onSubmit }) => {
  const [selected, setSelected] = useState("");
  const [customText, setCustomText] = useState("");

  const isCustom = selected === CUSTOM_INPUT_KEY;
  const finalAnswer = isCustom ? customText.trim() : selected.trim();
  const submit = () => { if (finalAnswer) onSubmit(pending.pendingId, finalAnswer); };

  const hasOtherOption = (pending.options ?? []).some((o) => o.startsWith("其他"));

  return (
    <Card title="🤔 需要你的澄清" style={{ borderColor: "#1677ff", marginTop: 8 }}>
      <p>{pending.question}</p>
      {pending.reason && <p style={{ color: "#888", fontSize: 12 }}>{pending.reason}</p>}
      <Space direction="vertical" style={{ width: "100%" }}>
        {pending.options && pending.options.length > 0 ? (
          <>
            <Radio.Group value={selected} onChange={(e) => setSelected(e.target.value)}>
              <Space direction="vertical">
                {pending.options.map((o) =>
                  o.startsWith("其他") ? (
                    <Radio key={o} value={CUSTOM_INPUT_KEY}>其他（自定义输入）</Radio>
                  ) : (
                    <Radio key={o} value={o}>{o}</Radio>
                  )
                )}
                {!hasOtherOption && <Radio value={CUSTOM_INPUT_KEY}>其他（自定义输入）</Radio>}
              </Space>
            </Radio.Group>
            {isCustom && (
              <Input.TextArea
                rows={3}
                value={customText}
                onChange={(e) => setCustomText(e.target.value)}
                placeholder="输入你的回答..."
                autoFocus
              />
            )}
          </>
        ) : (
          <Input.TextArea rows={3} value={customText} onChange={(e) => { setCustomText(e.target.value); setSelected(CUSTOM_INPUT_KEY); }} placeholder="输入回答..." />
        )}
        <Button type="primary" onClick={submit} disabled={!finalAnswer}>submit</Button>
      </Space>
    </Card>
  );
};
