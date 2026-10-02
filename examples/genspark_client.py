#!/usr/bin/env python3
import requests
import json
from typing import Dict, Any, List, Optional

class GensparkQGISClient:
    def __init__(self, base_url: str = "http://localhost:8000"):
        self.base_url = base_url
        self.session = requests.Session()
    
    def health_check(self) -> bool:
        try:
            resp = self.session.get(f"{self.base_url}/health")
            return resp.status_code == 200
        except Exception as e:
            print(f"Erro ao conectar: {e}")
            return False
    
    def get_status(self) -> Dict[str, Any]:
        resp = self.session.get(f"{self.base_url}/status")
        resp.raise_for_status()
        return resp.json()
    
    def list_tools(self) -> List[Dict[str, str]]:
        resp = self.session.get(f"{self.base_url}/tools")
        resp.raise_for_status()
        return resp.json()["tools"]
    
    def execute(self, tool_name: str, arguments: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        payload = {
            "tool_name": tool_name,
            "arguments": arguments or {}
        }
        resp = self.session.post(f"{self.base_url}/execute", json=payload)
        resp.raise_for_status()
        return resp.json()
    
    def ping(self) -> bool:
        result = self.execute("ping")
        return result.get("success", False)
    
    def create_new_project(self, name: str) -> Dict[str, Any]:
        return self.execute("create_new_project", {"name": name})
    
    def get_project_info(self) -> Dict[str, Any]:
        return self.execute("get_project_info")
    
    def get_layers(self) -> List[Dict[str, Any]]:
        result = self.execute("get_layers")
        return result.get("result", {}).get("layers", [])
    
    def add_vector_layer(self, file_path: str) -> Dict[str, Any]:
        return self.execute("add_vector_layer", {"file_path": file_path})
    
    def get_qgis_info(self) -> Dict[str, Any]:
        result = self.execute("get_qgis_info")
        return result.get("result", {})


if __name__ == "__main__":
    client = GensparkQGISClient()
    
    print("Testando conexão...")
    if client.health_check():
        print("Servidor respondendo!")
        
        status = client.get_status()
        print(f"Status: {json.dumps(status, indent=2)}")
        
        tools = client.list_tools()
        print(f"{len(tools)} ferramentas disponíveis")
    else:
        print("Servidor não respondendo")
