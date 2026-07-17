import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { EnumDictionaryTab } from "./EnumDictionaryTab";
import { enumApi } from "@/api";

vi.mock("@/api", () => ({
  enumApi: {
    listEnumDictionaries: vi.fn(),
    createEnumDictionary: vi.fn(),
    updateEnumCanonical: vi.fn(),
    deleteEnumCanonical: vi.fn(),
  },
}));

const mockList = enumApi.listEnumDictionaries as ReturnType<typeof vi.fn>;

describe("EnumDictionaryTab", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockList.mockResolvedValue({
      items: [
        {
          id: 1,
          enum_class_name: "OrderStatus",
          values: [{ name: "PAID", db_value: 1 }],
          source: "code",
          status: "canonical",
          reference_count: 3,
        },
        {
          id: 2,
          enum_class_name: "UserRole",
          values: [{ name: "ADMIN", db_value: 0 }, { name: "USER", db_value: 1 }],
          source: "manual",
          status: "canonical",
          reference_count: 0,
        },
      ],
      total: 2,
    });
  });

  it("renders enum list from API", async () => {
    render(<EnumDictionaryTab namespaceId={1} dbType="mongodb" />);
    await waitFor(() => screen.getByText("OrderStatus"));
    expect(screen.getByText("UserRole")).toBeInTheDocument();
    expect(screen.getByText("代码")).toBeInTheDocument();
    expect(screen.getByText("手动")).toBeInTheDocument();
  });

  it("opens create modal when 新建枚举 clicked", async () => {
    render(<EnumDictionaryTab namespaceId={1} dbType="mongodb" />);
    await waitFor(() => screen.getByText("OrderStatus"));
    fireEvent.click(screen.getByRole("button", { name: "新建枚举" }));
    expect(screen.getByText("新建枚举", { selector: ".ant-modal-title" })).toBeInTheDocument();
  });

  it("calls listEnumDictionaries with namespace_id", async () => {
    render(<EnumDictionaryTab namespaceId={42} dbType="mysql" />);
    await waitFor(() => expect(mockList).toHaveBeenCalledWith({ namespace_id: 42 }));
  });
});
