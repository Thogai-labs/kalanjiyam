"""
Surya OCR GPU Configuration

This module provides configuration options for Surya OCR GPU usage.
You can customize these settings based on your hardware and requirements.
"""

import os
from typing import Dict, Any, Optional
import logging


def get_default_gpu_config() -> Dict[str, Any]:
    """
    Get default GPU configuration for Surya OCR.
    
    Returns:
        Dictionary with default GPU configuration
    """
    return {
        'device': 'auto',  # 'auto', 'cuda:0', 'cuda:1', 'cpu'
        'memory_fraction': 0.8,  # Use 80% of GPU memory
        'max_memory_mb': 0,  # 0 = no limit
        'allow_growth': True,  # Allow GPU memory to grow as needed
    }


def get_gpu_config_from_env() -> Dict[str, Any]:
    """
    Get GPU configuration from environment variables.
    
    Environment Variables:
        SURYA_GPU_DEVICE: GPU device to use ('auto', 'cuda:0', 'cuda:1', 'cpu', or just '0', '1', '2')
        SURYA_GPU_MEMORY_FRACTION: Fraction of GPU memory to use (0.0-1.0)
        SURYA_GPU_MAX_MEMORY_MB: Maximum GPU memory in MB (0 = no limit)
        SURYA_GPU_ALLOW_GROWTH: Whether to allow GPU memory growth ('true'/'false')
        SURYA_MAX_IMAGE_SIZE: Maximum image dimension (default: 2048)
        SURYA_MATH_MODE: Enable math recognition ('true'/'false')
    
    Returns:
        Dictionary with GPU configuration from environment
    """
    config = get_default_gpu_config()
    
    # Override with environment variables
    if os.environ.get('SURYA_GPU_DEVICE'):
        device = os.environ['SURYA_GPU_DEVICE']
        # Handle numeric GPU IDs (e.g., "2" -> "cuda:2")
        if device.isdigit():
            config['device'] = f'cuda:{device}'
        else:
            config['device'] = device
        logging.info(f"GPU device set from environment: {config['device']}")
    
    if os.environ.get('SURYA_GPU_MEMORY_FRACTION'):
        try:
            config['memory_fraction'] = float(os.environ['SURYA_GPU_MEMORY_FRACTION'])
        except ValueError:
            pass
    
    if os.environ.get('SURYA_GPU_MAX_MEMORY_MB'):
        try:
            config['max_memory_mb'] = int(os.environ['SURYA_GPU_MAX_MEMORY_MB'])
        except ValueError:
            pass
    
    if os.environ.get('SURYA_GPU_ALLOW_GROWTH'):
        config['allow_growth'] = os.environ['SURYA_GPU_ALLOW_GROWTH'].lower() == 'true'
    
    # Auto-detect GPU if device is 'auto'
    if config['device'] == 'auto':
        if os.environ.get('CUDA_VISIBLE_DEVICES'):
            # Use the first available GPU
            gpu_id = os.environ.get('CUDA_VISIBLE_DEVICES').split(',')[0]
            config['device'] = f'cuda:{gpu_id}'
        else:
            # Check if CUDA is available
            try:
                import torch
                if torch.cuda.is_available():
                    config['device'] = 'cuda:0'
                else:
                    config['device'] = 'cpu'
            except ImportError:
                config['device'] = 'cpu'
    
    return config


def get_multi_gpu_config(gpu_ids: list, memory_fraction: float = 0.8) -> Dict[str, Any]:
    """
    Get configuration for multi-GPU setup.
    
    Args:
        gpu_ids: List of GPU IDs to use (e.g., [0, 1, 2])
        memory_fraction: Fraction of GPU memory to use per GPU
    
    Returns:
        Dictionary with multi-GPU configuration
    """
    return {
        'device': f'cuda:{gpu_ids[0]}',  # Use first GPU as primary
        'memory_fraction': memory_fraction,
        'max_memory_mb': 0,
        'allow_growth': True,
        'multi_gpu': True,
        'gpu_ids': gpu_ids,
    }


def get_cpu_config() -> Dict[str, Any]:
    """
    Get CPU-only configuration for Surya OCR.
    
    Returns:
        Dictionary with CPU configuration
    """
    return {
        'device': 'cpu',
        'memory_fraction': 1.0,
        'max_memory_mb': 0,
        'allow_growth': False,
    }


def validate_gpu_config(config: Dict[str, Any]) -> bool:
    """
    Validate GPU configuration.
    
    Args:
        config: GPU configuration dictionary
    
    Returns:
        True if configuration is valid, False otherwise
    """
    # Check device
    device = config['device']
    if device not in ['auto', 'cpu'] and not device.startswith('cuda:'):
        # Handle numeric GPU IDs
        if device.isdigit():
            config['device'] = f'cuda:{device}'
        else:
            return False
    
    # Check memory fraction
    if not 0.0 <= config['memory_fraction'] <= 1.0:
        return False
    
    # Check max memory
    if config['max_memory_mb'] < 0:
        return False
    
    return True


def print_gpu_config(config: Dict[str, Any]) -> None:
    """
    Print GPU configuration in a readable format.
    
    Args:
        config: GPU configuration dictionary
    """
    print("Surya OCR GPU Configuration:")
    print(f"  Device: {config['device']}")
    print(f"  Memory Fraction: {config['memory_fraction']}")
    print(f"  Max Memory: {config['max_memory_mb']} MB" if config['max_memory_mb'] > 0 else "  Max Memory: No limit")
    print(f"  Allow Growth: {config['allow_growth']}")
    
    if 'multi_gpu' in config and config['multi_gpu']:
        print(f"  Multi-GPU: Yes (GPUs: {config['gpu_ids']})")


def setup_gpu_environment(config: Dict[str, Any]) -> None:
    """
    Setup GPU environment variables for Surya OCR.
    
    Args:
        config: GPU configuration dictionary
    """
    # Normalize device name
    device = config['device']
    if device.isdigit():
        device = f'cuda:{device}'
        config['device'] = device
    
    # Set device
    if device.startswith('cuda'):
        # Extract GPU ID and set CUDA_VISIBLE_DEVICES
        if ':' in device:
            gpu_id = device.split(':')[1]
            os.environ['CUDA_VISIBLE_DEVICES'] = gpu_id
            # When CUDA_VISIBLE_DEVICES is set, the GPU becomes cuda:0 in PyTorch
            os.environ['TORCH_DEVICE'] = 'cuda:0'
            logging.info(f"Set CUDA_VISIBLE_DEVICES={gpu_id}, TORCH_DEVICE=cuda:0")
        else:
            os.environ['CUDA_VISIBLE_DEVICES'] = '0'
            os.environ['TORCH_DEVICE'] = 'cuda:0'
            logging.info("Set CUDA_VISIBLE_DEVICES=0, TORCH_DEVICE=cuda:0")
        
        # Set memory management
        if config['allow_growth']:
            os.environ['TF_FORCE_GPU_ALLOW_GROWTH'] = 'true'
        
        # Set memory fraction if specified
        if config['memory_fraction'] < 1.0:
            os.environ['TF_GPU_MEMORY_FRACTION'] = str(config['memory_fraction'])
        
        logging.info(f"GPU configured: {device}, memory fraction: {config['memory_fraction']}")
    else:
        os.environ['TORCH_DEVICE'] = 'cpu'
        logging.info("Using CPU for Surya OCR")


# Example configurations
EXAMPLE_CONFIGS = {
    'conservative': {
        'device': 'cuda:0',
        'memory_fraction': 0.5,  # Use only 50% of GPU memory
        'max_memory_mb': 4096,   # Max 4GB
        'allow_growth': False,
    },
    'balanced': {
        'device': 'cuda:0',
        'memory_fraction': 0.8,  # Use 80% of GPU memory
        'max_memory_mb': 0,      # No limit
        'allow_growth': True,
    },
    'performance': {
        'device': 'cuda:0',
        'memory_fraction': 1.0,  # Use all GPU memory
        'max_memory_mb': 0,      # No limit
        'allow_growth': True,
    },
    'multi_gpu_2': get_multi_gpu_config([0, 1], 0.6),
    'multi_gpu_4': get_multi_gpu_config([0, 1, 2, 3], 0.5),
}
