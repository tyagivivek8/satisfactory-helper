import { Component, type ErrorInfo, type ReactNode } from "react";
import { Button } from "./ui/button";

interface Props {
  children: ReactNode;
}

interface State {
  error: Error | null;
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("Satisfactory Helper UI failed", error, info);
  }

  render() {
    if (this.state.error) {
      return (
        <main className="fatal-state">
          <p className="instrument-label">Workbench fault</p>
          <h1>The interface hit an unexpected error.</h1>
          <pre>{this.state.error.message}</pre>
          <Button type="button" onClick={() => window.location.reload()}>
            Reload workbench
          </Button>
        </main>
      );
    }
    return this.props.children;
  }
}
