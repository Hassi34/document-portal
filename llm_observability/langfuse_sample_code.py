from dotenv import load_dotenv
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from langfuse import get_client, observe
from langfuse.langchain import CallbackHandler

# Load environment variables
load_dotenv()


@observe()  # Automatically log function as a trace to Langfuse
def process_user_query(user_input: str):
    langfuse = get_client()

    # Update trace attributes
    langfuse.update_current_trace(
        name="user-query-processing",
        session_id="session-1234",
        user_id="user-5678",
        input={"query": user_input},
    )

    # Initialize the Langfuse handler - automatically inherits the current trace context
    langfuse_handler = CallbackHandler()

    # Your Langchain code - will be nested under the @observe trace
    llm = ChatOpenAI(model_name="gpt-4o")
    prompt = ChatPromptTemplate.from_template("Respond to: {input}")
    chain = prompt | llm

    result = chain.invoke(
        {"input": user_input}, config={"callbacks": [langfuse_handler]}
    )

    # Update trace with final output
    langfuse.update_current_trace(output={"response": result.content})

    return result.content


@observe(name="joke-generator", as_type="generation")
def generate_joke(topic: str):
    # Initialize the Langfuse handler
    langfuse_handler = CallbackHandler()

    # Create LangChain components
    llm = ChatOpenAI(model_name="gpt-4o")
    prompt = ChatPromptTemplate.from_template("Tell me a joke about {topic}")
    chain = prompt | llm

    # Run chain with metadata
    response = chain.invoke(
        {"topic": topic},
        config={
            "callbacks": [langfuse_handler],
            "metadata": {
                "langfuse_user_id": "user_123",
                "langfuse_session_id": "session_abc",
                "langfuse_tags": ["test", "langchain", "jokes"],
            },
        },
    )

    return response.content


def main():
    # Verify connection
    langfuse = get_client()
    if langfuse.auth_check():
        print("Langfuse client is authenticated and ready!")
    else:
        print("Authentication failed. Please check your credentials and host.")
        return

    # Test the decorated functions
    print("Testing user query processing...")
    answer = process_user_query("What is the capital of France?")
    print(f"Answer: {answer}")

    print("\nTesting joke generation...")
    joke = generate_joke("cats")
    print(f"Joke: {joke}")

    # Flush events in short-lived applications
    langfuse.flush()
    print("\nTraces sent to Langfuse!")


if __name__ == "__main__":
    main()
