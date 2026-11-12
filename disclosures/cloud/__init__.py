"""Everything that depends on the cloud provider. No other code imports a cloud SDK.

    storage.BlobStore      copies data files to and from a bucket
    warehouse.Warehouse    runs SQL over the insider-trade table
    llm.chat_model         returns the language model, as a LangChain chat model

Each module holds the interface, today's implementations and a factory that picks one from
the settings. Porting to another cloud means adding an implementation here and rewriting
`infra/`.
"""
