import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ChatPanel } from "./ChatPanel";

afterEach(cleanup);

function renderPanel(onSend = vi.fn()) {
  render(
    <ChatPanel
      messages={[]}
      events={[]}
      running={false}
      disabled={false}
      error={null}
      onSend={onSend}
      onCancel={vi.fn()}
    />,
  );
  return { onSend, input: screen.getByLabelText("Describe the production outcome") };
}

describe("ChatPanel", () => {
  it("submits a trimmed outcome request with Enter", () => {
    const { input, onSend } = renderPanel();
    fireEvent.change(input, { target: { value: "  Produce 10 computers per minute  " } });
    fireEvent.keyDown(input, { key: "Enter" });

    expect(onSend).toHaveBeenCalledOnce();
    expect(onSend).toHaveBeenCalledWith("Produce 10 computers per minute");
    expect(input).toHaveValue("");
  });

  it("keeps Shift+Enter available for multi-line requests", () => {
    const { input, onSend } = renderPanel();
    fireEvent.change(input, { target: { value: "Use the current site" } });
    fireEvent.keyDown(input, { key: "Enter", shiftKey: true });

    expect(onSend).not.toHaveBeenCalled();
    expect(input).toHaveValue("Use the current site");
  });
});
