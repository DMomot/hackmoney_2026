// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

interface IERC1155 {
    function safeTransferFrom(address from, address to, uint256 id, uint256 amount, bytes calldata data) external;
}

contract PremarketRouter {
    address public owner;

    // platformId → wallet address
    mapping(uint8 => address) public platformWallets;

    // Order intent: token_id, side, amount, platform — all in one event
    event OrderIntent(
        address indexed user,
        uint8   indexed platformId,
        address token,
        uint256 amount,
        bytes   metadata  // abi.encode(marketId, tokenId, side, ...)
    );

    modifier onlyOwner() {
        require(msg.sender == owner, "not owner");
        _;
    }

    constructor() {
        owner = msg.sender;
    }

    // Safe ERC20 transferFrom + emit intent
    function transferERC20(
        address token,
        address from,
        uint8 platformId,
        uint256 amount,
        bytes calldata metadata
    ) external onlyOwner {
        address to = platformWallets[platformId];
        require(to != address(0), "platform not set");
        (bool ok, bytes memory ret) = token.call(
            abi.encodeWithSelector(0x23b872dd, from, to, amount)
        );
        require(ok && (ret.length == 0 || abi.decode(ret, (bool))), "transfer failed");
        emit OrderIntent(from, platformId, token, amount, metadata);
    }

    // Transfer ERC1155 + emit intent
    function transferERC1155(
        address token,
        address from,
        uint8 platformId,
        uint256 tokenId,
        uint256 amount,
        bytes calldata metadata
    ) external onlyOwner {
        address to = platformWallets[platformId];
        require(to != address(0), "platform not set");
        IERC1155(token).safeTransferFrom(from, to, tokenId, amount, "");
        emit OrderIntent(from, platformId, token, amount, metadata);
    }

    // Pull user tokens → approve LiFi Diamond → call Diamond with calldata → bridge starts
    function bridgeViaLiFi(
        address token,
        address from,
        uint256 amount,
        address lifiDiamond,
        bytes calldata lifiData,
        bytes calldata metadata
    ) external onlyOwner {
        // Pull tokens from user to this contract
        (bool ok1, bytes memory r1) = token.call(
            abi.encodeWithSelector(0x23b872dd, from, address(this), amount)
        );
        require(ok1 && (r1.length == 0 || abi.decode(r1, (bool))), "pull failed");

        // Approve LiFi Diamond to spend tokens
        (bool ok2,) = token.call(
            abi.encodeWithSelector(0x095ea7b3, lifiDiamond, amount)
        );
        require(ok2, "approve failed");

        // Call LiFi Diamond — triggers the bridge
        (bool ok3,) = lifiDiamond.call(lifiData);
        require(ok3, "lifi call failed");

        emit OrderIntent(from, 0, token, amount, metadata);
    }

    function setPlatformWallet(uint8 platformId, address wallet) external onlyOwner {
        platformWallets[platformId] = wallet;
    }

    function setOwner(address newOwner) external onlyOwner {
        require(newOwner != address(0), "zero address");
        owner = newOwner;
    }
}
