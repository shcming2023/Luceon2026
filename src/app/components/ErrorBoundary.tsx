import React, { Component, ErrorInfo, ReactNode } from 'react';

interface Props {
  children: ReactNode;
}

interface State {
  hasError: boolean;
  error?: Error;
  errorInfo?: ErrorInfo;
}

/**
 * 全局错误边界组件。
 * 捕获子组件树中的 JS 运行时错误，展示友好的错误页面，
 * 提供"重试"（不刷新页面）和"刷新页面"两种恢复方式。
 */
export class ErrorBoundary extends Component<Props, State> {
  constructor(props: Props) {
    super(props);
    this.state = { hasError: false };
  }

  static getDerivedStateFromError(error: Error): Partial<State> {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, errorInfo: ErrorInfo): void {
    console.error('[ErrorBoundary] Uncaught error:', error, errorInfo);
    this.setState({ errorInfo });
  }

  handleRetry = () => {
    this.setState({ hasError: false, error: undefined, errorInfo: undefined });
  };

  render(): ReactNode {
    if (this.state.hasError) {
      return (
        <div style={{
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
          height: '100vh',
          padding: '20px',
          backgroundColor: '#f5f5f5',
          color: '#333'
        }}>
          <h1 style={{ color: '#d32f2f', marginBottom: '16px' }}>出错了</h1>
          <p style={{ marginBottom: '24px', textAlign: 'center' }}>
            应用程序遇到了一个意外错误
          </p>
          {this.state.error && (
            <details style={{
              marginBottom: '24px',
              padding: '16px',
              backgroundColor: 'white',
              borderRadius: '4px',
              border: '1px solid #ddd',
              maxWidth: '600px',
              width: '100%'
            }}>
              <summary style={{ cursor: 'pointer', marginBottom: '8px' }}>
                错误详情
              </summary>
              <pre style={{
                overflow: 'auto',
                fontSize: '12px',
                color: '#666',
                whiteSpace: 'pre-wrap',
                wordBreak: 'break-word',
              }}>
                {this.state.error.toString()}
                {this.state.errorInfo?.componentStack && (
                  `\n\nComponent Stack:${this.state.errorInfo.componentStack}`
                )}
              </pre>
            </details>
          )}
          <div style={{ display: 'flex', gap: '12px' }}>
            <button
              onClick={this.handleRetry}
              style={{
                padding: '12px 24px',
                backgroundColor: '#388e3c',
                color: 'white',
                border: 'none',
                borderRadius: '4px',
                cursor: 'pointer',
                fontSize: '14px'
              }}
            >
              重试
            </button>
            <button
              onClick={() => window.location.reload()}
              style={{
                padding: '12px 24px',
                backgroundColor: '#1976d2',
                color: 'white',
                border: 'none',
                borderRadius: '4px',
                cursor: 'pointer',
                fontSize: '14px'
              }}
            >
              刷新页面
            </button>
          </div>
        </div>
      );
    }

    return this.props.children;
  }
}
