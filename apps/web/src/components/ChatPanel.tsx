import { ArrowUp, Square, WandSparkles } from "lucide-react";
import { useMemo, useState, type FormEvent, type KeyboardEvent } from "react";
import type { StreamEvent } from "../types";
import { Button } from "./ui/button";
import { ScrollArea } from "./ui/scroll-area";
import { Textarea } from "./ui/textarea";

export interface ConversationMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
}

interface Props {
  messages: ConversationMessage[];
  events: StreamEvent[];
  running: boolean;
  disabled: boolean;
  error: string | null;
  onSend: (message: string) => void;
  onCancel: () => void;
}

const suggestions = [
  "What should I improve next without rebuilding everything?",
  "Help me increase the output of the site I am looking at.",
  "Which hard-drive choice is actually useful at my current progress?",
];

export function ChatPanel({
  messages,
  events,
  running,
  disabled,
  error,
  onSend,
  onCancel,
}: Props) {
  const [value, setValue] = useState("");
  const latestProgress = useMemo(
    () => [...events].reverse().find((event) => event.message)?.message,
    [events],
  );

  function submit(event?: FormEvent) {
    event?.preventDefault();
    const message = value.trim();
    if (!message || running || disabled) return;
    setValue("");
    onSend(message);
  }

  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      submit();
    }
  }

  return (
    <section className="chat-panel" aria-label="Factory planning conversation">
      <div className="panel-heading">
        <div>
          <p className="instrument-label">Outcome</p>
          <h1>What needs to change?</h1>
        </div>
        <WandSparkles aria-hidden="true" size={18} />
      </div>

      <ScrollArea className="transcript" aria-live="polite">
        <article className="transcript-entry transcript-entry--assistant">
          <span className="speaker">Helper</span>
          <p>
            Tell me the result you want. I’ll inspect this save, choose whether to reuse,
            expand, move, or rebuild, then map the changes to the actual site and floors.
          </p>
        </article>
        {messages.map((message) => (
          <article key={message.id} className={`transcript-entry transcript-entry--${message.role}`}>
            <span className="speaker">{message.role === "user" ? "You" : "Helper"}</span>
            <p>{message.content}</p>
          </article>
        ))}
        {running && (
          <article className="transcript-entry transcript-entry--progress">
            <span className="speaker live-indicator">Working</span>
            <p>{latestProgress ?? "Inspecting the current factory…"}</p>
            <div className="progress-track" aria-hidden="true">
              <span />
            </div>
          </article>
        )}
        {error && (
          <article className="transcript-entry transcript-entry--error">
            <span className="speaker">Couldn’t finish</span>
            <p>{error}</p>
          </article>
        )}
        {messages.length === 0 && !running && (
          <div className="prompt-suggestions" aria-label="Example questions">
            {suggestions.map((suggestion) => (
              <Button
                key={suggestion}
                className="prompt-suggestion"
                type="button"
                variant="ghost"
                onClick={() => setValue(suggestion)}
              >
                {suggestion}
              </Button>
            ))}
          </div>
        )}
      </ScrollArea>

      <form className="composer" onSubmit={submit}>
        <label htmlFor="factory-request">Describe the production outcome</label>
        <Textarea
          id="factory-request"
          value={value}
          onChange={(event) => setValue(event.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Produce 10 Computers/min at this site and reuse what already fits…"
          rows={4}
          disabled={disabled}
        />
        <div className="composer-footer">
          <span>{disabled ? "Sign in to a planning agent to continue" : "Enter to plan · Shift+Enter for a new line"}</span>
          {running ? (
            <Button className="composer-stop" type="button" variant="secondary" onClick={onCancel}>
              <Square aria-hidden="true" size={12} fill="currentColor" /> Stop
            </Button>
          ) : (
            <Button className="composer-send" type="submit" disabled={disabled || !value.trim()}>
              Plan <ArrowUp aria-hidden="true" size={15} />
            </Button>
          )}
        </div>
      </form>
    </section>
  );
}
