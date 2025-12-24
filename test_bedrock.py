import boto3
import json

def test_claude():
    # 東京リージョンを指定
    client = boto3.client("bedrock-runtime", region_name="ap-northeast-1")
    
    model_id = "anthropic.claude-3-5-sonnet-20240620-v1:0"
    
    prompt = "あなたは熱血なAI助手フレイミーです。挨拶をしてください。"
    
    body = json.dumps({
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 1000,
        "messages": [
            {
                "role": "user",
                "content": prompt
            }
        ]
    })

    try:
        response = client.invoke_model(body=body, modelId=model_id)
        response_body = json.loads(response.get("body").read())
        print("--- Claudeからの返答 ---")
        print(response_body.get("content")[0].get("text"))
    except Exception as e:
        print(f"エラーが発生しました: {e}")

if __name__ == "__main__":
    test_claude()