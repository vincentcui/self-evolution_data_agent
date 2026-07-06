import { describe, it, expect, vi, beforeEach, type Mock } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";

// 隔离测试: mock @/api 模块 (组件行为测试), 与 datasourceApi.test.ts 的 axios 传输层 L1 契约测试互补
vi.mock("@/api", () => ({
  probeDatasource: vi.fn(),
  addDataSource: vi.fn(),
}));

import { probeDatasource } from "@/api";
import DataSourceFormModal from "./DataSourceFormModal";

const mockProbe = probeDatasource as unknown as Mock;

/** 填连接字段, 使 handleProbe 的 validateFields([连接字段]) 通过 */
function fillConnectionFields() {
  // db_type Select: 打开下拉选 MySQL
  // db_type Select: 找不到即 throw (硬失败, 防 antd DOM 变动致静默 no-op → 测试假绿)
  const dbTypeCombobox = document.querySelector<HTMLElement>("#db_type");
  if (!dbTypeCombobox) throw new Error("db_type Select input not found");
  fireEvent.mouseDown(dbTypeCombobox);
  // 选中第一个下拉项 (MySQL)
  const option = document.querySelector<HTMLElement>(".ant-select-item-option");
  if (!option) throw new Error("db_type dropdown option not rendered");
  fireEvent.click(option);

  // 文本字段按 antd 生成的 id = 字段名 填值 (找不到即 throw, 同上防静默降级)
  const setById = (id: string, value: string) => {
    const el = document.getElementById(id) as HTMLInputElement | null;
    if (!el) throw new Error(`field #${id} not found`);
    fireEvent.change(el, { target: { value } });
  };
  setById("host", "localhost");
  setById("port", "3306");
  setById("database", "shop_db");
  setById("username", "u");
  setById("password", "p");
}

function renderModal(overrides: Partial<React.ComponentProps<typeof DataSourceFormModal>> = {}) {
  const onCancel = vi.fn();
  const onSubmitted = vi.fn();
  render(
    <DataSourceFormModal
      open={true}
      activeNsId={3}
      onCancel={onCancel}
      onSubmitted={onSubmitted}
      {...overrides}
    />,
  );
  return { onCancel, onSubmitted };
}

/** 取「确定」按钮 (antd footer primary 按钮; antd 会在两个中文字间插空格→"确 定", 故用正则) */
function getOkButton(): HTMLButtonElement {
  return screen.getByRole("button", { name: /确\s*定/ }) as HTMLButtonElement;
}

describe("DataSourceFormModal 状态机", () => {
  beforeEach(() => vi.clearAllMocks());

  it("初始态: 确定按钮置灰", () => {
    renderModal();
    expect(getOkButton()).toBeDisabled();
  });

  it("probe ok (含 detected_timezone): 时区回填 + 确定可点", async () => {
    mockProbe.mockResolvedValueOnce({
      connected: true,
      detected_timezone: "Asia/Shanghai",
      failure_reason: null,
    });
    renderModal();
    fillConnectionFields();
    fireEvent.click(screen.getByTestId("probe-btn"));

    await waitFor(() => expect(mockProbe).toHaveBeenCalledTimes(1));
    // probe body 契约: 含真实填入的连接字段(证明 Select/输入生效) 且不含 timezone(后端 DataSourceProbeIn)
    const probeBody = mockProbe.mock.calls[0][1];
    expect(probeBody).toMatchObject({ db_type: "mysql", host: "localhost", database: "shop_db" });
    expect(probeBody).not.toHaveProperty("timezone");
    // 时区回填
    await waitFor(() => {
      const tz = document.getElementById("timezone") as HTMLInputElement | null;
      expect(tz?.value).toBe("Asia/Shanghai");
    });
    // 确定可点
    await waitFor(() => expect(getOkButton()).not.toBeDisabled());
  });

  it("probe need-tz (detected_timezone=null): 红字提示 + 确定置灰, 选时区后恢复", async () => {
    mockProbe.mockResolvedValueOnce({
      connected: true,
      detected_timezone: null,
      failure_reason: null,
    });
    renderModal();
    fillConnectionFields();
    fireEvent.click(screen.getByTestId("probe-btn"));

    await waitFor(() => expect(screen.getByText("必须选择时区")).toBeInTheDocument());
    expect(getOkButton()).toBeDisabled();

    // 手动选时区 → 红字消失 + 确定可点
    const tz = document.getElementById("timezone") as HTMLInputElement;
    fireEvent.change(tz, { target: { value: "Asia/Shanghai" } });

    await waitFor(() => expect(screen.queryByText("必须选择时区")).not.toBeInTheDocument());
    await waitFor(() => expect(getOkButton()).not.toBeDisabled());
  });

  it("probe fail (connected=false): 显示 failure_reason + 确定置灰", async () => {
    mockProbe.mockResolvedValueOnce({
      connected: false,
      detected_timezone: null,
      failure_reason: "Access denied",
    });
    renderModal();
    fillConnectionFields();
    fireEvent.click(screen.getByTestId("probe-btn"));

    await waitFor(() => expect(screen.getByText("Access denied")).toBeInTheDocument());
    expect(getOkButton()).toBeDisabled();
  });

  it("probe need-tz: 焦点跳时区框 (design 组件F)", async () => {
    mockProbe.mockResolvedValueOnce({
      connected: true,
      detected_timezone: null,
      failure_reason: null,
    });
    renderModal();
    fillConnectionFields();
    fireEvent.click(screen.getByTestId("probe-btn"));

    await waitFor(() => expect(screen.getByText("必须选择时区")).toBeInTheDocument());
    const tz = document.getElementById("timezone") as HTMLInputElement;
    await waitFor(() => expect(document.activeElement).toBe(tz));
  });

  it("probe ok 后清空时区: 确定重新置灰 (防 ok 态绕过时区校验)", async () => {
    mockProbe.mockResolvedValueOnce({
      connected: true,
      detected_timezone: "Asia/Shanghai",
      failure_reason: null,
    });
    renderModal();
    fillConnectionFields();
    fireEvent.click(screen.getByTestId("probe-btn"));

    // ok 态时区自动回填 + 确定可点
    await waitFor(() => expect(getOkButton()).not.toBeDisabled());

    // 手动清空时区 → 确定重新置灰 (canSubmit 统一要求时区非空)
    const tz = document.getElementById("timezone") as HTMLInputElement;
    fireEvent.change(tz, { target: { value: "" } });

    await waitFor(() => expect(getOkButton()).toBeDisabled());
  });
});
